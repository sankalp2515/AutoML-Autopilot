"""Matched-budget repair evaluation; full external grading stays the same in all arms."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
from typing import Callable

ARMS={"A":{"mode":"generic","internal_ml":False,"research_enabled":False},
      "D":{"mode":"full","internal_ml":True,"research_enabled":True},
      "D-ML":{"mode":"full","internal_ml":False,"research_enabled":True},
      "D-TAV":{"mode":"full","internal_ml":True,"research_enabled":False}}

def digest_file(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def wilson(successes,total):
    if not total:return None
    z=1.96;p=successes/total;denom=1+z*z/total
    center=(p+z*z/(2*total))/denom;half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/denom
    return [round(max(0,center-half),6),round(min(1,center+half),6)]

def summarize(rows, *, expected=None):
    # Every attempted slot, including infrastructure/provider failures, is in denominator.
    groups=[]
    for arm in ARMS:
        selected=[r for r in rows if r["arm"]==arm]
        accepted=[r for r in selected if r.get("accepted")]
        resolved=sum(r.get("external_decision")=="VERIFIED" for r in accepted)
        false=sum(r.get("external_decision")=="REJECTED" for r in accepted)
        unknown=sum(r.get("external_decision") not in {"VERIFIED","REJECTED"} for r in accepted)
        costs=[r.get("cost_usd") if r.get("cost_complete",True) else None for r in selected]
        complete_cost=bool(selected) and all(isinstance(c,(int,float)) for c in costs)
        groups.append({"arm":arm,"attempted":len(selected),"accepted":len(accepted),"verified":resolved,"false_accepts":false,"unresolved_accepts":unknown,
            "verified_rate":resolved/len(selected) if selected else None,"verified_ci95":wilson(resolved,len(selected)),
            "false_accept_rate":false/len(accepted) if accepted else None,"false_accept_ci95":wilson(false,len(accepted)),
            "cost_usd":sum(costs) if complete_cost else None,
            "cost_per_verified":sum(costs)/resolved if complete_cost and resolved else None})
    keys=[(r["fixture"],r["seed"],r["arm"]) for r in rows]
    if len(keys)!=len(set(keys)):raise ValueError("duplicate evaluation slot")
    expected_keys={(f,s,a) for f,s,_ in keys for a in ARMS}
    matched=len(keys)==len(expected_keys) and set(keys)==expected_keys
    return {"schema_version":1,"kind":"autonomous-comparison","arms":groups,"rows":rows,"expected_slots":expected,
            "complete":expected is not None and len(rows)==expected and matched,"limitations":["Rates describe these tasks/seeds only; fixtures are not independent real-world samples.","False acceptance denominator is arm-accepted patches; unresolved acceptance is separately reported.","Costs are provider token-price estimates excluding compute/storage/research; missing costs are not zero."]}

def run_slot(*, agent, oracle, fixture, seed, arm):
    if arm not in ARMS:raise ValueError("unknown arm")
    row={"fixture":fixture,"seed":seed,"arm":arm,"accepted":False,"external_decision":"INCONCLUSIVE","cost_usd":None}
    try:
        report=agent.run();row["agent_decision"]=report.decision.value
        external_receipt=None
        row["accepted"]=report.finalist is not None and report.decision.value=="VERIFIED"
        if row["accepted"]:
            candidate=next(c for c in report.candidates if c.candidate.candidate_id==report.finalist)
            # Full arms already hold a final external-grade receipt. No second holdout opening.
            receipt=report.final_ml_receipt or oracle.ml(report.finalist,candidate.candidate.diff,final=True)
            external_receipt=receipt.model_dump(mode="json") if hasattr(receipt,"model_dump") else {"decision":receipt.decision.value}
            row["external_decision"]=("VERIFIED" if candidate.code_receipt and candidate.code_receipt.decision.value=="VERIFIED" and receipt.decision.value=="VERIFIED" else receipt.decision.value)
        row.update(cost_usd=report.usage.get("total_est_cost_usd"),seconds=report.seconds,task_id=report.task_id,
                   provider=report.provider,models=report.models,prompt_hash=report.prompt_hash,runner=report.runner,
                   limits=agent.ecc.limits.model_dump(),snapshot_digest=agent.ecc.repo_snapshot_digest)
        payload=report.model_dump(mode="json")
        row["cost_complete"]=bool(payload.get("calls")) and all(call.get("ok") for call in payload["calls"])
        payload["evaluation"]={"arm":arm,"arm_accepted":row["accepted"],"external_decision":row["external_decision"],"external_ml_receipt":external_receipt}
        return row,payload
    except Exception as exc:
        row["error"]=type(exc).__name__ # don't persist provider messages/secrets
        row.update(provider=agent.llm.provider,models=agent.llm.models,limits=agent.ecc.limits.model_dump(),snapshot_digest=agent.ecc.repo_snapshot_digest)
        report=agent.report.model_dump(mode="json")
        report.update(decision="INCONCLUSIVE",reasons=["E_EVALUATION_INFRASTRUCTURE"],usage=agent.llm.usage(),calls=[x.model_dump(mode="json") for x in agent.llm.calls])
        row["cost_complete"]=bool(report["calls"]) and all(call.get("ok") for call in report["calls"])
        row["cost_usd"]=report["usage"].get("total_est_cost_usd") if all(call.get("ok") for call in report["calls"]) else None
        return row,report
