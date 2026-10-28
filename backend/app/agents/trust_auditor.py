"""TrustAuditor — the Skeptic. Runs the deterministic falsification probes against
a completed run's model + data and produces a graded Trust Report.

It ships the *source* of `app/core/trust_probes.py` into the sandbox (the only
place with the ML stack + the data files), appends a bootstrap that calls
`run_all(...)` with this run's paths, and persists the returned report.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agents.base_agent import BaseAgent
from app.redis_client import get_run_state, set_trust_report

# Read the probe source once at import (text only — never imported, so the
# backend image's lack of pandas/sklearn doesn't matter).
_PROBES_SRC = (Path(__file__).resolve().parents[1] / "core" / "trust_probes.py").read_text(encoding="utf-8")


def _summarize(report: dict[str, Any]) -> str:
    if report.get("error"):
        return report["error"]
    probes = report.get("probes", [])
    fails = [p for p in probes if p["status"] == "fail"]
    warns = [p for p in probes if p["status"] == "warn"]
    head = f"Trust grade {report.get('grade')} ({report.get('trust_score')}/100)."
    if fails:
        return head + " Critical issues: " + "; ".join(p["headline"] for p in fails[:3])
    if warns:
        return head + " Cautions: " + "; ".join(p["headline"] for p in warns[:3])
    return head + " No material trust issues found — the result holds up to scrutiny."


def _error_report(msg: str) -> dict[str, Any]:
    return {
        "schema_version": 1, "grade": "?", "trust_score": None, "probes": [],
        "n_pass": 0, "n_warn": 0, "n_fail": 0, "n_skip": 0, "error": msg,
        "summary": msg, "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def gate_decision(report: dict[str, Any]) -> dict[str, Any]:
    """Inline-gate verdict from a trust report: is the result untrustworthy, and
    which columns look like leakage (so the gate can auto-exclude + re-run)?"""
    probes = report.get("probes", [])
    leak = next((p for p in probes if p.get("id") == "leakage"), None)
    leaked: list[str] = []
    if leak:
        for s in leak.get("evidence", {}).get("suspicious_features", []):
            if isinstance(s, dict) and s.get("abs_corr_with_target", 0) >= 0.98 and s.get("feature"):
                leaked.append(str(s["feature"]))
    untrustworthy = report.get("grade") in ("D", "F") or (leak is not None and leak.get("status") == "fail")
    return {"untrustworthy": bool(untrustworthy), "leaked_columns": leaked, "grade": report.get("grade")}


class TrustAuditor(BaseAgent):
    name = "trust_auditor"

    async def audit(self, run_id: str) -> dict[str, Any]:
        state = await get_run_state(run_id) or {}
        enriched = state.get("enriched_data_path")
        model_path = state.get("tuned_model_path") or state.get("winner_model_path")
        if not enriched or not model_path:
            report = _error_report("No completed model/data found for this run — nothing to audit.")
            await set_trust_report(run_id, report)
            return report
        return await self.audit_from(
            run_id, enriched, model_path,
            state.get("task_type"), state.get("primary_metric"),
            state.get("baseline_score"), state.get("current_score", state.get("final_score")),
            state.get("score_std", 0.0), state.get("evaluation_basis", "holdout"),
        )

    async def audit_from(self, run_id, enriched, model_path, task_type, primary_metric,
                         baseline, final, std, basis) -> dict[str, Any]:
        await self.emit(run_id, "Running trust audit — the skeptic is probing for leakage, overfit and fragility…")

        boot = (
            "\nRESULT = run_all("
            f"enriched_path={enriched!r}, model_path={model_path!r}, "
            f"task_type={task_type!r}, primary_metric={primary_metric!r}, "
            f"baseline_score={baseline!r}, final_score={final!r}, "
            f"score_std={std!r}, evaluation_basis={basis!r})\n"
        )
        result = await self.execute_code(_PROBES_SRC + boot, run_id, timeout=300)

        if not result.get("success") or not isinstance(result.get("result"), dict):
            report = _error_report(f"Trust audit failed to run: {str(result.get('error'))[:300]}")
        else:
            report = result["result"]
            report["summary"] = _summarize(report)
            report["generated_at"] = datetime.now(timezone.utc).isoformat()

        await set_trust_report(run_id, report)
        await self.emit(
            run_id,
            f"Trust audit complete — grade {report.get('grade')} "
            f"({report.get('n_fail', 0)} fail / {report.get('n_warn', 0)} warn)",
            {"grade": report.get("grade"), "trust_score": report.get("trust_score")},
        )
        return report
