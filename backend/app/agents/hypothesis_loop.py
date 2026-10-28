"""Phase 2 — the Hypothesis Loop (the agentic layer).

The Strategist (LLM) reasons about THIS dataset + the Skeptic's findings and
proposes falsifiable hypotheses, each mapped to a bounded, safe test primitive.
The Conductor budgets them, the Experimenter runs the primitives in the sandbox,
the Critic renders verdicts, and one adaptive follow-up round drills into what
was confirmed. Reasoning is open-ended; execution is deterministic and safe.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agents.base_agent import BaseAgent
from app.core import investigation as inv
from app.redis_client import get_run_state, get_trust_report, set_investigation_report

_PRIM_SRC = (Path(__file__).resolve().parents[1] / "core" / "investigation_primitives.py").read_text(encoding="utf-8")

_STRATEGIST_SYSTEM = (
    "You are a skeptical senior ML scientist investigating whether a trained model can be trusted "
    "and how it could fail. Propose FALSIFIABLE hypotheses about THIS specific dataset/model. "
    "You may ONLY test a hypothesis with one of the provided primitives, referencing REAL column "
    "names from the provided list. Respond as JSON: "
    '{"hypotheses":[{"id","statement","rationale","severity":"low|medium|high",'
    '"primitive","params":{...},"expected":{"direction":">|<|>=|<=","threshold":<number>}}]}. '
    "Set `expected` so that, if the hypothesis is TRUE, the primitive's observed value satisfies it."
)


def _dossier(state: dict, trust: dict | None, known_cols: list[str]) -> str:
    audit = state.get("data_audit") or {}
    shape = audit.get("shape")
    lines = [
        f"Task type: {state.get('task_type')}",
        f"Target: {state.get('target_column')}",
        f"Primary metric: {state.get('primary_metric')}",
        f"Winner model: {state.get('winner_model')}",
        f"Baseline -> final: {state.get('baseline_score')} -> {state.get('current_score', state.get('final_score'))}",
        f"Dataset shape (rows, cols): {shape}",
        f"Available columns ({len(known_cols)}): {', '.join(known_cols[:60])}",
    ]
    if trust and trust.get("probes"):
        flagged = [p for p in trust["probes"] if p.get("status") in ("fail", "warn")]
        if flagged:
            lines.append("Skeptic flagged: " + "; ".join(f"{p['title']}: {p['headline']}" for p in flagged[:6]))
    lines.append("\nTest primitives you may use:\n" + inv.catalog_text())
    return "\n".join(lines)


def _known_columns(state: dict, trust: dict | None) -> list[str]:
    cols: list[str] = []
    for f in (state.get("shap_top_features") or []):
        cols.append(str(f))
    audit = state.get("data_audit") or {}
    for c in (audit.get("columns") or audit.get("feature_names") or []):
        cols.append(str(c))
    if trust:
        for p in trust.get("probes", []):
            for s in (p.get("evidence", {}).get("suspicious_features") or []):
                if isinstance(s, dict) and s.get("feature"):
                    cols.append(str(s["feature"]))
    seen, out = set(), []
    for c in cols:
        if c not in seen:
            seen.add(c); out.append(c)
    return out


class HypothesisLoop(BaseAgent):
    name = "hypothesis_loop"

    async def _propose(self, system: str, user: str, budget_left: int) -> list[dict]:
        try:
            obj = await self.llm.complete_json(system, user)
        except Exception:
            return []
        return inv.select(inv.parse_hypotheses(obj), {"max_hypotheses": budget_left, "max_followups": 0, "max_llm_calls": 0})

    async def _experiment(self, run_id: str, enriched: str, model_path: str, task_type: str, hyps: list[dict]) -> dict[str, dict]:
        bounded = [h for h in hyps if h["primitive"] != inv.CUSTOM_PRIMITIVE]
        custom = [h for h in hyps if h["primitive"] == inv.CUSTOM_PRIMITIVE]
        out: dict[str, dict] = {}
        if bounded:
            specs = [{"id": h["id"], "primitive": h["primitive"], "params": h["params"]} for h in bounded]
            boot = f"\nRESULT = run_primitives({enriched!r}, {model_path!r}, {task_type!r}, {specs!r})\n"
            result = await self.execute_code(_PRIM_SRC + boot, run_id, timeout=300)
            if result.get("success") and isinstance(result.get("result"), list):
                out.update({r["id"]: r for r in result["result"] if isinstance(r, dict) and "id" in r})
        for h in custom:
            out[h["id"]] = await self._experiment_custom(run_id, enriched, model_path, task_type, h)
        return out

    async def _experiment_custom(self, run_id: str, enriched: str, model_path: str,
                                 task_type: str, hyp: dict) -> dict:
        """Phase 2.5 escalation: the Experimenter WRITES a small sandboxed test for a
        hypothesis no bounded primitive covers. One repair retry on failure; any
        residual error becomes an inconclusive verdict, never a crash."""
        desc = str(hyp.get("params", {}).get("description", ""))[:800]
        base_user = (
            f"Hypothesis to test: {hyp.get('statement', '')}\n"
            f"Test instructions: {desc}\n\n"
            f"Context: dataset_path = {enriched!r} (CSV with a '__target__' column), "
            f"model_path = {model_path!r} (joblib), task_type = {task_type!r}.\n"
            "Write a SHORT Python test (pandas/numpy/sklearn/joblib only). Sample to <=20000 "
            "rows, seed everything with 42, and set RESULT = {\"observed\": <float>, "
            "\"evidence\": {<small dict>}}. observed must be the ONE number the hypothesis's "
            "`expected` threshold refers to."
        )
        error = ""
        for attempt in range(2):
            user = base_user if not error else (
                base_user + f"\n\nYour previous attempt failed with:\n{error[:600]}\nFix it.")
            try:
                obj = await self.llm.complete_json(
                    "You write minimal, defensive Python test scripts for a restricted ML sandbox "
                    "(no os/open/network/eval). Respond as JSON: "
                    '{"code": "<complete runnable script setting RESULT>"}.',
                    user,
                )
            except Exception as exc:
                return {"id": hyp["id"], "observed": None, "error": f"LLM unavailable: {str(exc)[:120]}"}
            code = obj.get("code") if isinstance(obj, dict) else None
            if not isinstance(code, str) or not code.strip():
                error = "LLM returned no code"
                continue
            result = await self.execute_code(code, run_id, timeout=180, restricted=True)
            data = self.result_dict(result)
            if result.get("success") and isinstance(data.get("observed"), (int, float)):
                ev = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
                return {"id": hyp["id"], "observed": float(data["observed"]),
                        "evidence": {**ev, "custom_code": True}}
            error = str(result.get("error") or "RESULT.observed missing/non-numeric")
        return {"id": hyp["id"], "observed": None, "error": f"custom test failed: {error[:200]}"}

    def _verdicts(self, hyps: list[dict], results: dict[str, dict]) -> list[dict]:
        out = []
        for h in hyps:
            r = results.get(h["id"], {})
            v = inv.verdict(h, r.get("observed"), r.get("error"))
            out.append({**h, **v, "evidence": r.get("evidence", {})})
        return out

    async def investigate(self, run_id: str) -> dict[str, Any]:
        state = await get_run_state(run_id) or {}
        enriched = state.get("enriched_data_path")
        model_path = state.get("tuned_model_path") or state.get("winner_model_path")
        if not enriched or not model_path:
            report = inv.aggregate(run_id, [], {"llm_calls": 0})
            report["error"] = "No completed model/data found for this run."
            report["summary"] = report["error"]
            await set_investigation_report(run_id, report)
            return report

        trust = await get_trust_report(run_id)
        known = _known_columns(state, trust)
        task_type = state.get("task_type")
        budget = inv.DEFAULT_BUDGET
        llm_calls = 0

        await self.emit(run_id, "Investigation: the strategist is forming hypotheses about this dataset…")
        dossier = _dossier(state, trust, known)

        # Phase 3 — cross-run memory: prior findings on this dataset sharpen the
        # Strategist (skip refuted ground, drill into confirmed weaknesses).
        from app.core import run_memory
        mem_sig = run_memory.signature_for_run(run_id, state.get("dataset_path", ""))
        brief = run_memory.memory_brief(run_memory.load_memory(mem_sig))
        if brief:
            dossier += "\n\nPRIOR FINDINGS on this dataset (earlier runs):\n" + brief
        selected = await self._propose(_STRATEGIST_SYSTEM, dossier, budget["max_hypotheses"])
        llm_calls += 1

        results = await self._experiment(run_id, enriched, model_path, task_type, selected) if selected else {}
        verdicts = self._verdicts(selected, results)

        # One adaptive follow-up round, seeded by what was confirmed.
        confirmed = [v for v in verdicts if v["verdict"] == "confirmed"]
        if confirmed and llm_calls < budget["max_llm_calls"]:
            await self.emit(run_id, "Investigation: drilling into confirmed findings…")
            follow_user = (
                dossier + "\n\nCONFIRMED so far:\n"
                + "\n".join(f"- {c['statement']} (observed {c.get('observed')})" for c in confirmed[:5])
                + "\n\nPropose up to 3 DEEPER hypotheses that build on these confirmed findings."
            )
            extra = await self._propose(_STRATEGIST_SYSTEM, follow_user, 3)
            llm_calls += 1
            # avoid repeating identical tests
            done = {(v["primitive"], tuple(sorted(v["params"].items()))) for v in verdicts}
            extra = [h for h in extra if (h["primitive"], tuple(sorted(h["params"].items()))) not in done]
            if extra:
                r2 = await self._experiment(run_id, enriched, model_path, task_type, extra)
                verdicts += self._verdicts(extra, r2)

        report = inv.aggregate(run_id, verdicts, {"llm_calls": llm_calls, "hypotheses": len(verdicts)})
        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        await set_investigation_report(run_id, report)
        run_memory.record_investigation(mem_sig, run_id, verdicts)
        await self.emit(
            run_id,
            f"Investigation complete — {report['n_confirmed']} confirmed / {report['n_refuted']} refuted",
            {"n_confirmed": report["n_confirmed"], "n_refuted": report["n_refuted"]},
        )
        return report
