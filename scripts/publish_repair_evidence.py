"""Build bounded public evidence views from measured artifacts; never fabricate runs."""
import argparse
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def build(comparison=None):
    proofs=[]
    for p in sorted((ROOT/"docs/benchmarks/repair/fixture-proofs").glob("*.json")):
        d=json.loads(p.read_text())
        if d.get("kind")!="checker-proof":continue
        r=d["receipts"]
        proofs.append({"fixture":d["fixture"],"kind":d["kind"],"autonomous_agent":d["autonomous_agent"],"runner":d["runner"],
          "wrong_code":r["wrong_fix"]["code"]["decision"],"wrong_ml":r["wrong_fix"]["ml"]["decision"],"reference_code":r["reference_fix"]["code"]["decision"],"reference_ml":r["reference_fix"]["ml"]["decision"],"source":p.relative_to(ROOT).as_posix(),"sha256":hashlib.sha256(p.read_bytes()).hexdigest()})
    subset_path=ROOT/"docs/benchmarks/repair/mlops-subset-proof.json"
    subset=json.loads(subset_path.read_text()) if subset_path.exists() else None
    measured=None
    if comparison:
        d=json.loads(Path(comparison).read_text())
        if d.get("kind")!="autonomous-comparison" or not d.get("complete") or not d.get("rows"):raise ValueError("only a complete measured comparison can be published")
        for r in d["rows"]:
            if r.get("provider") in {None,"scripted","scripted-ui-qa"} or not r.get("report_sha256"):raise ValueError("missing genuine model provenance")
            report=Path(comparison).parent/f"{r['fixture']}-{r['seed']}-{r['arm']}"/"report.json"
            if hashlib.sha256(report.read_bytes()).hexdigest()!=r["report_sha256"]:raise ValueError("report changed")
        measured=d
    return {"schema_version":1,"checker_proofs":proofs,"public_subset":subset,"comparison":measured}

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--comparison");args=p.parse_args()
    out=ROOT/"frontend/data/repair/evidence.json";out.write_text(json.dumps(build(args.comparison),indent=2),encoding="utf-8")
