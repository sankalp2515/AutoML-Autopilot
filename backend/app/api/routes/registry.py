"""Model Registry — completed runs as versioned models, grouped by dataset family,
with champion/challenger status (MLOps view over the existing runs + deployments).

A "model family" = runs on the same dataset signature (same columns). Within a family:
- CHAMPION  = the run with a currently-active deployment.
- CHALLENGER = any other completed run (a candidate that could be promoted).
Promotion/rollback reuse the existing deploy/undeploy endpoints — this route is the
read model + the "is the challenger genuinely better than the champion" comparison
(on each run's honest holdout final_score, not CV).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select

from app.core import metric_registry
from app.core import run_memory
from app.database import AsyncSessionLocal
from app.models.run import Deployment, Run

router = APIRouter(prefix="/api/v1/registry", tags=["registry"])


@router.get("")
async def list_registry() -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        runs = (await db.execute(
            select(Run).where(Run.status == "completed").order_by(Run.created_at.asc())
        )).scalars().all()
        active = {d.run_id for d in (await db.execute(
            select(Deployment).where(Deployment.status == "active")
        )).scalars().all()}

    # Group by dataset signature (best-effort; falls back to the dataset filename).
    families: dict[str, dict] = {}
    for r in runs:
        sig = run_memory.signature_for_run(r.id, r.dataset_path or "") or f"file:{r.dataset_filename}"
        fam = families.setdefault(sig, {"signature": sig, "dataset": r.dataset_filename,
                                        "task_type": r.task_type, "primary_metric": r.primary_metric,
                                        "versions": []})
        fam["versions"].append({
            "run_id": r.id,
            "version": len(fam["versions"]) + 1,
            "algorithm": r.winner_model,
            "score": r.final_score,
            "primary_metric": r.primary_metric,
            "trust_grade": getattr(r, "trust_grade", None),
            "status": "champion" if r.id in active else "challenger",
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    # Per family: mark the champion + flag whether the best challenger genuinely
    # beats it on the honest holdout metric (directional).
    out = []
    for fam in families.values():
        metric = fam.get("primary_metric") or ""
        champ = next((v for v in fam["versions"] if v["status"] == "champion"), None)
        best_challenger = None
        if champ and champ.get("score") is not None:
            for v in fam["versions"]:
                if v["status"] != "champion" and v.get("score") is not None:
                    gain = metric_registry.directional_gain(v["score"], champ["score"], metric)
                    if gain > 0 and (best_challenger is None or v["score"] != champ["score"]):
                        if best_challenger is None or \
                           metric_registry.directional_gain(v["score"], best_challenger["score"], metric) > 0:
                            best_challenger = v
        fam["champion_run_id"] = champ["run_id"] if champ else None
        fam["promotable_challenger"] = (
            {"run_id": best_challenger["run_id"], "score": best_challenger["score"],
             "beats_champion_by": round(metric_registry.directional_gain(
                 best_challenger["score"], champ["score"], metric), 4)}
            if (champ and best_challenger) else None
        )
        # newest version first for display
        fam["versions"] = list(reversed(fam["versions"]))
        out.append(fam)

    # Families with a deployed champion first, then by most versions.
    out.sort(key=lambda f: (f["champion_run_id"] is None, -len(f["versions"])))
    return {"families": out}
