"""Publish an actual model report into the bundled replay catalog, with provenance."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"backend"))
from pydantic_settings.sources import DotEnvSettingsSource
DotEnvSettingsSource.__call__=lambda self:{}
from app.repair import contracts as c

def publish(source, replay_id, sample):
    if not re.fullmatch(r"[a-z0-9_-]{3,80}",replay_id):raise ValueError("invalid replay id")
    path=Path(source).resolve();report=json.loads(path.read_text(encoding="utf-8"))
    if report.get("provider") not in {"nvidia_api","nebius_token_factory"} or not any(x.get("ok") for x in report.get("calls",[])):
        raise ValueError("requires an actual successful model call")
    evaluation=report.get("evaluation") or {}
    external=evaluation.get("external_ml_receipt")
    if external and report.get("finalist"):
        candidate=next(x for x in report["candidates"] if x["candidate"]["candidate_id"]==report["finalist"])
        code=c.CodeReceipt.model_validate(candidate["code_receipt"])
        ml=c.MLReceipt.model_validate(external)
        if ml.task_id==code.task_id: # reproduction receipts keep their own admission id, do not forge a combined receipt
            combined=c.VerifiedChangeReceipt.combine(code,ml,ml_required=True)
            report.update(final_ml_receipt=external,decision=combined.decision.value,reasons=combined.reasons,verdict=combined.model_dump(mode="json"))
            report["events"].append({"seq":max([e.get("seq",0) for e in report["events"]] or [0])+1,"type":"ml_oracle.rejected" if ml.decision.value=="REJECTED" else "ml_oracle.passed","actor":"ml_oracle","candidate_id":report["finalist"],"evaluation_after_agent_finished":True})
    report.update(recorded=True,replay_id=replay_id,sample=sample,
                  source_file=path.relative_to(ROOT).as_posix(),source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    target=ROOT/"frontend/data/repair/generated-replays.json"
    catalog=json.loads(target.read_text()) if target.exists() else {}
    catalog[replay_id]=report;target.write_text(json.dumps(catalog,indent=2),encoding="utf-8")

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("report");p.add_argument("--id",required=True);p.add_argument("--sample",required=True);a=p.parse_args();publish(a.report,a.id,a.sample)
