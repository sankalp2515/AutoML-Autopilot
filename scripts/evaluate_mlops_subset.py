"""Pinned public MLOps-Bench subset harness. No model calls or leaderboard claims.

Agent input is problem.json + repo/ ONLY. Public F2P/P2P tests are evaluator-side.
This is benchmark feasibility/patch scoring, not a hostile-code security boundary.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
CORPUS=ROOT / "fixtures/external/mlops-bench"

def validate_sources(root=CORPUS):
    manifest=json.loads((root / "provenance.json").read_text())
    for rel,digest in manifest["files"].items():
        path=(root/rel).resolve()
        if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError("upstream bytes differ: " + rel)
    return manifest

def score(task, workspace, suite):
    with tempfile.TemporaryDirectory(prefix="mlops-grade-") as tmp:
        work=Path(tmp)/"work";shutil.copytree(workspace,work)
        # Exact upstream test files; never put the oracle or metadata into work.
        tests=Path(tmp)/"tests";tests.mkdir()
        shutil.copyfile(task/"tests"/f"test_{suite}.py",tests/f"test_{suite}.py")
        env={**os.environ,"PYTHONPATH":str(work),"PYTEST_DISABLE_PLUGIN_AUTOLOAD":"1"}
        report=Path(tmp)/"report.xml"
        result=subprocess.run([sys.executable,"-m","pytest","-q",str(tests),f"--junitxml={report}"],cwd=work,env=env,capture_output=True,text=True,timeout=120)
        diagnostics=(result.stdout+"\n"+result.stderr)[-12000:]
        if not report.exists(): return {"passed":0,"failed":0,"errors":1,"exit_code":result.returncode,"parsed":False,"diagnostics":diagnostics}
        cases=list(ET.parse(report).getroot().iter("testcase"))
        return {"passed":sum(not any(c.find(k) is not None for k in ["failure","error","skipped"]) for c in cases),
                "failed":sum(c.find("failure") is not None for c in cases),"errors":sum(c.find("error") is not None for c in cases),
                "skipped":sum(c.find("skipped") is not None for c in cases),"exit_code":result.returncode,"parsed":bool(cases),
                **({"diagnostics":diagnostics} if any(c.find("error") is not None for c in cases) else {})}

def feasibility(root=CORPUS):
    source=validate_sources(root);rows=[]
    for entry in source["tasks"]:
        task=root/entry["id"]
        row={"task":entry["id"],"upstream_path":entry["upstream_path"],"baseline":{},"reference":{}}
        for kind,folder in [("baseline","repo"),("reference","oracle")]:
            row[kind]={suite:score(task,task/folder,suite) for suite in ["f2p","p2p"]}
        row["feasibility_passed"]=row["baseline"]["f2p"]["failed"]==3 and row["baseline"]["p2p"]["passed"]==4 and all(row["reference"][suite]["passed"]==n and row["reference"][suite]["exit_code"]==0 for suite,n in [("f2p",3),("p2p",4)])
        rows.append(row)
    return {"kind":"public-subset-feasibility","autonomous_agent":False,"leaderboard_comparable":False,"source_commit":source["commit"],"tasks":rows}

def main():
    ap=argparse.ArgumentParser(description=__doc__);sub=ap.add_subparsers(dest="command",required=True)
    proof=sub.add_parser("prove");proof.add_argument("--out",required=True)
    export=sub.add_parser("export-agent");export.add_argument("task");export.add_argument("--out",required=True)
    grade=sub.add_parser("score");grade.add_argument("task");grade.add_argument("--workspace",required=True);grade.add_argument("--out",required=True)
    args=ap.parse_args();manifest=validate_sources()
    if args.command=="prove": result=feasibility()
    else:
        if args.task not in {x["id"] for x in manifest["tasks"]}:ap.error("unknown pinned task")
        task=CORPUS/args.task
        if args.command=="export-agent":
            out=Path(args.out)
            if out.exists():ap.error("export directory must not exist")
            out.mkdir(parents=True);shutil.copytree(task/"repo",out/"repo");shutil.copyfile(task/"problem.json",out/"problem.json");return
        result={"kind":"public-subset-patch-score","task":args.task,"source_commit":manifest["commit"],"leaderboard_comparable":False,"tests":{s:score(task,Path(args.workspace),s) for s in ["f2p","p2p"]}}
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,indent=2),encoding="utf-8")
    if args.command=="prove" and not all(t["feasibility_passed"] for t in result["tasks"]):raise SystemExit(1)

if __name__=="__main__":main()
