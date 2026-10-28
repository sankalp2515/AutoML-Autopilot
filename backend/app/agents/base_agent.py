import json
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.core import context
from app.core import metrics
from app.core.llm import get_llm
from app.core.logging import get_logger
from app.core import mlflow_tracker as mlflow
from app.core.state import AgentState, DecisionLogEntry
from app.database import AsyncSessionLocal
from app.models.run import AgentStep, DecisionLog, Run
from app.redis_client import publish_progress
from app.sandbox.executor import get_executor

# ── Tier-1 self-repair micro-loop ─────────────────────────────────────────────
# When a sandbox execution fails, instead of failing the whole pipeline we ask
# the LLM to revise the AGENT'S DECISION (never to write free code), re-render
# the SAME vetted template with the corrected parameters, and retry. Capped at
# MAX_REPAIRS attempts, after which we fail fast as before. Because repairs stay
# inside the template vocabulary, the sandbox safety model is untouched.
MAX_REPAIRS = 2

_AGENTIC_WRITE_SYSTEM = """You are a senior ML engineer writing Python that runs in a
restricted sandbox. The vetted template failed on this dataset; write code that handles
the actual data and succeeds. Be defensive (NaN, dtype, shape edge cases). Use ONLY the
allowed libraries, set the required RESULT dict, and never use os/open/eval/network.
Respond with JSON: {"diagnosis": "<one line>", "code": "<complete runnable script>"}."""

REPAIR_SYSTEM_PROMPT = """You are a senior ML engineer debugging a failed pipeline step.

A block of code failed with an error. The code was generated from a FIXED, trusted
template that was parameterized by a set of DECISION PARAMETERS (JSON). You CANNOT
change the code or the template — you can ONLY revise the decision parameters to
avoid the error (e.g. change an encoding strategy, drop an offending column, switch
a model that errored, relax an imputation choice).

You MUST respond with a JSON object containing exactly these keys:
{
  "diagnosis": "<one sentence: what went wrong>",
  "revised_params": { <the SAME keys as the params you were given, with corrected values> },
  "change_summary": "<one sentence: what you changed and why it should fix it>"
}

Rules:
- revised_params MUST contain every key from the original params (keep keys you don't change).
- Make the MINIMAL change that addresses the error. Do not gratuitously alter working choices.
- Stay within the allowed value vocabulary implied by the original values.
"""


class BaseAgent:
    name: str = "base_agent"

    def __init__(self) -> None:
        self.llm = get_llm()
        self.executor = get_executor()
        self._log = get_logger(self.name)

    async def run(self, state: AgentState) -> dict[str, Any]:
        raise NotImplementedError

    # ── Boundary validation helpers (untrusted LLM/sandbox output) ────────────
    # Normalize once at the boundary so downstream access is safe by construction
    # (see the feature_engineer post-mortem: "string indices must be integers").

    @staticmethod
    def norm_dicts(seq: Any) -> list[dict]:
        """Keep only dict entries of an alleged list-of-dicts."""
        return [e for e in (seq if isinstance(seq, list) else []) if isinstance(e, dict)]

    @staticmethod
    def norm_str_list(seq: Any, key: str = "column") -> list[str]:
        """Coerce an alleged list of strings; dict entries contribute their `key`."""
        out: list[str] = []
        for e in (seq if isinstance(seq, list) else []):
            if isinstance(e, str) and e:
                out.append(e)
            elif isinstance(e, dict):
                v = e.get(key) or e.get("name") or e.get("col")
                if isinstance(v, str) and v:
                    out.append(v)
        return out

    @staticmethod
    def norm_str_map(d: Any) -> dict[str, str]:
        """Coerce an alleged {str: str} mapping; non-conforming entries dropped."""
        if not isinstance(d, dict):
            return {}
        return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str)}

    @staticmethod
    def norm_choice(v: Any, allowed: set[str], default: str) -> str:
        """Clamp an alleged enum value to the allowed vocabulary."""
        return v if isinstance(v, str) and v in allowed else default

    @staticmethod
    def result_dict(result: dict[str, Any]) -> dict:
        """The sandbox RESULT as a dict, whatever came back."""
        data = result.get("result")
        return data if isinstance(data, dict) else {}

    # ── Instrumented wrappers ─────────────────────────────────────────────────

    async def execute_code(
        self, code: str, run_id: str, timeout: int = 120, restricted: bool = False
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        result = await self.executor.execute(code, run_id, timeout, restricted=restricted)
        elapsed = time.perf_counter() - t0

        status = "success" if result.get("success") else (
            "timeout" if "timed out" in str(result.get("error", "")) else "error"
        )
        metrics.sandbox_executions_total.labels(status=status).inc()
        metrics.sandbox_duration_seconds.observe(elapsed)

        if not result.get("success"):
            self._log.warning(
                "sandbox_execution_failed",
                run_id=run_id,
                agent=self.name,
                status=status,
                error=str(result.get("error", ""))[:300],
                duration_s=round(elapsed, 2),
            )
        else:
            self._log.debug(
                "sandbox_execution_ok",
                run_id=run_id,
                agent=self.name,
                duration_s=round(elapsed, 2),
            )
        return result

    async def execute_code_with_repair(
        self,
        run_id: str,
        render_fn: Callable[[dict], str],
        params: dict,
        repair_goal: str,
        timeout: int = 120,
        max_repairs: int = MAX_REPAIRS,
    ) -> dict[str, Any]:
        """Execute a templated step with Tier-1 self-repair.

        render_fn(params) -> code string. On failure, the LLM revises `params`
        (same keys) and we re-render + retry, up to `max_repairs` times. The
        returned dict adds: repair_attempts (int), repaired (bool),
        final_params (dict), repair_history (list).
        """
        attempt = 0
        current_params = dict(params)
        repair_history: list[dict] = []

        while True:
            code = render_fn(current_params)
            result = await self.execute_code(code, run_id, timeout)

            if result.get("success"):
                result["repair_attempts"] = attempt
                result["repaired"] = attempt > 0
                result["final_params"] = current_params
                result["repair_history"] = repair_history
                if attempt > 0:
                    metrics.agent_repairs_total.labels(
                        agent_name=self.name, outcome="recovered"
                    ).inc()
                    self._log.info(
                        "self_repair_recovered", run_id=run_id, agent=self.name,
                        attempts=attempt,
                    )
                return result

            if attempt >= max_repairs:
                metrics.agent_repairs_total.labels(
                    agent_name=self.name, outcome="exhausted"
                ).inc()
                self._log.error(
                    "self_repair_exhausted", run_id=run_id, agent=self.name,
                    attempts=attempt, error=str(result.get("error", ""))[:200],
                )
                result["repair_attempts"] = attempt
                result["repaired"] = False
                result["repair_history"] = repair_history
                return result

            # Ask the LLM to revise the decision parameters
            attempt += 1
            error_text = str(result.get("error", ""))[-1500:]  # tail = the actual traceback
            await self.emit(
                run_id,
                f"Step failed — attempting self-repair ({attempt}/{max_repairs})...",
                {"attempt": attempt},
            )
            try:
                revision = await self.llm.complete_json(
                    REPAIR_SYSTEM_PROMPT,
                    f"Goal of this step: {repair_goal}\n\n"
                    f"Decision parameters (JSON):\n{json.dumps(current_params, default=str, indent=2)}\n\n"
                    f"The code failed with this error:\n```\n{error_text}\n```\n\n"
                    f"Return the revised parameters.",
                )
            except Exception as exc:
                self._log.error("self_repair_llm_failed", run_id=run_id,
                                agent=self.name, error=str(exc)[:200])
                result["repair_attempts"] = attempt - 1
                result["repaired"] = False
                result["repair_history"] = repair_history
                return result

            revised = revision.get("revised_params")
            if not isinstance(revised, dict) or set(revised.keys()) != set(current_params.keys()):
                # LLM didn't honor the contract — keep keys it got right, ignore the rest
                if isinstance(revised, dict):
                    current_params = {k: revised.get(k, current_params[k]) for k in current_params}
                else:
                    # Unusable revision; record and let the next loop hit the cap
                    repair_history.append({
                        "attempt": attempt,
                        "diagnosis": revision.get("diagnosis", ""),
                        "change_summary": "LLM returned no usable revised_params",
                    })
                    continue
            else:
                current_params = revised

            repair_history.append({
                "attempt": attempt,
                "diagnosis": revision.get("diagnosis", "")[:300],
                "change_summary": revision.get("change_summary", "")[:300],
            })
            self._log.info(
                "self_repair_attempt", run_id=run_id, agent=self.name,
                attempt=attempt, diagnosis=revision.get("diagnosis", "")[:120],
            )
            # Record the repair as a decision so it appears in the evidence trail
            await self._log_decision(
                run_id=run_id,
                decision=f"Self-repair #{attempt}: {revision.get('change_summary', '')[:160]}",
                reasoning=f"Diagnosis: {revision.get('diagnosis', '')[:300]}",
                result_summary=f"repair attempt {attempt}/{max_repairs}",
            )

    async def emit(self, run_id: str, message: str, data: dict | None = None) -> None:
        payload = {
            "agent": self.name,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data or {},
        }
        self._log.info("agent_progress", run_id=run_id, agent=self.name, message=message)
        await publish_progress(run_id, payload)

    async def _mark_step(
        self, run_id: str, status: str, error: str | None = None
    ) -> None:
        now = datetime.now(timezone.utc)

        if status == "running":
            # Bind this asyncio task to (run_id, agent) so every LLM call made
            # while this agent runs is attributed correctly — even when other
            # runs execute concurrently. Replaces shared singleton attributes.
            context.set_agent_context(run_id, self.name, time.perf_counter())
            self._log.info("agent_started", run_id=run_id, agent=self.name)

        elif status in ("completed", "failed"):
            elapsed = time.perf_counter() - context.get_agent_start()
            metrics.agent_runs_total.labels(agent_name=self.name, status=status).inc()
            metrics.agent_duration_seconds.labels(agent_name=self.name).observe(elapsed)

            if status == "completed":
                self._log.info(
                    "agent_completed",
                    run_id=run_id,
                    agent=self.name,
                    duration_s=round(elapsed, 2),
                )
            else:
                self._log.error(
                    "agent_failed",
                    run_id=run_id,
                    agent=self.name,
                    duration_s=round(elapsed, 2),
                    error=error,
                )

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(AgentStep).where(
                    AgentStep.run_id == run_id,
                    AgentStep.agent_name == self.name,
                )
            )
            step = result.scalar_one_or_none()

            if step is None:
                step = AgentStep(
                    run_id=run_id,
                    agent_name=self.name,
                    status=status,
                    started_at=now if status == "running" else None,
                    completed_at=now if status in ("completed", "failed") else None,
                    error_message=error,
                )
                db.add(step)
            else:
                step.status = status
                if status == "running":
                    step.started_at = now
                if status in ("completed", "failed"):
                    step.completed_at = now
                if error:
                    step.error_message = error

            await db.commit()

    async def _log_decision(
        self,
        run_id: str,
        decision: str,
        reasoning: str,
        code_executed: str = "",
        result_summary: str = "",
    ) -> DecisionLogEntry:
        now = datetime.now(timezone.utc)
        self._log.info(
            "decision",
            run_id=run_id,
            agent=self.name,
            decision=decision[:120],
            result=result_summary,
        )
        async with AsyncSessionLocal() as db:
            log = DecisionLog(
                run_id=run_id,
                agent_name=self.name,
                timestamp=now,
                decision=decision,
                reasoning=reasoning,
                code_executed=code_executed,
                result_summary=result_summary,
            )
            db.add(log)
            await db.commit()

        return DecisionLogEntry(
            agent=self.name,
            timestamp=now.isoformat(),
            decision=decision,
            reasoning=reasoning,
            code_executed=code_executed,
            result_summary=result_summary,
        )

    async def _update_run_field(self, run_id: str, **fields: Any) -> None:
        from sqlalchemy import select
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Run).where(Run.id == run_id))
            run = result.scalar_one_or_none()
            if run:
                for key, value in fields.items():
                    setattr(run, key, value)
                await db.commit()

    async def try_agentic_repair(
        self,
        run_id: str,
        code: str,
        failed_result: dict,
        *,
        result_keys: list[str],
        goal: str,
        task_type: str = "unknown",
        tags: list[str] | None = None,
        timeout: int = 180,
    ) -> dict[str, Any]:
        """Uniform template-first → agentic-repair fallback (Phase 2).

        Pass the failed sandbox `result`; if it failed, the agent WRITES a
        corrected script (restricted sandbox, cookbook-seeded) and retries until
        it produces a RESULT with `result_keys`. Returns a success result or the
        (still-failed) result. A no-op when `failed_result` already succeeded, so
        the happy path costs nothing. Every agent uses this so NO template error
        is fatal until the agent has tried to fix it.
        """
        if failed_result.get("success"):
            return failed_result
        return await self.execute_code_agentic(
            run_id, code, str(failed_result.get("error", "")),
            agent_role=self.name, task_type=task_type,
            tags=tags or [self.name], goal=goal,
            result_keys=result_keys, timeout=timeout,
        )

    # ── E3: agentic write/debug loop (template-first; this is the failure path) ──
    async def execute_code_agentic(
        self, run_id: str, failed_code: str, error: str, *,
        agent_role: str, task_type: str, tags: list[str], goal: str,
        result_keys: list[str], timeout: int = 180, max_attempts: int = 3,
    ) -> dict[str, Any]:
        """When a vetted template fails, the agent WRITES corrected code itself —
        seeded by prior fixes from the cookbook — runs it under the restricted
        sandbox, reads the traceback, and retries. Successful fixes are stored.
        Returns the sandbox result (with `agentic_used`), or a failure dict."""
        from app.core import code_cookbook

        fixes = code_cookbook.retrieve(agent_role, task_type, tags, keywords=error, k=2)
        # Layer 2: log the retrieved context (RAG-without-vectors) for traceability.
        self._log.info("context_retrieved", run_id=run_id, agent=self.name,
                       n_fixes=len(fixes), provenance=[f.get("provenance") for f in fixes])
        fixes_text = "\n\n".join(
            f"# prior fix (provenance={f.get('provenance')}, used {f.get('success_count')}x):\n"
            f"{f.get('code', '')[:1500]}"
            for f in fixes
        ) or "(no prior fixes found)"

        last_err = error
        last_code = failed_code
        for attempt in range(1, max_attempts + 1):
            await self.emit(run_id, f"Agent writing a fix ({attempt}/{max_attempts})…", {"agentic": attempt})
            user = (
                f"GOAL: {goal}\n\nThe vetted template failed. Write CORRECTED, complete Python "
                f"for the sandbox.\n\nSANDBOX CONTRACT:\n"
                f"- Preloaded vars: dataset_path (str CSV path), artifacts_dir (str dir), run_id (str), "
                f"and modules pd, np, json, joblib.\n"
                f"- You MUST set RESULT = a dict containing these keys: {result_keys}.\n"
                f"- Allowed imports ONLY: pandas, numpy, sklearn, xgboost, scipy, imblearn, joblib, "
                f"json, math, re, datetime, statistics, collections, itertools, warnings, random.\n"
                f"- FORBIDDEN: os, sys, subprocess, open(), eval, exec, network. Save files with "
                f'joblib.dump(obj, artifacts_dir + "/name.pkl").\n\n'
                f"PRIOR FIXES (adapt if relevant):\n{fixes_text}\n\n"
                f"FAILED CODE:\n```python\n{last_code[:2500]}\n```\n\n"
                f"TRACEBACK:\n```\n{last_err[-1500:]}\n```\n\n"
                f'Respond JSON: {{"diagnosis": "...", "code": "<full corrected script>"}}'
            )
            try:
                resp = await self.llm.complete_json(_AGENTIC_WRITE_SYSTEM, user)
            except Exception as exc:
                last_err = f"LLM write failed: {exc}"
                break
            code = resp.get("code", "")
            if not code or len(code) < 20:
                last_err = "LLM returned no code"
                continue

            result = await self.execute_code(code, run_id, timeout, restricted=True)
            res = result.get("result")
            ok = (result.get("success") and isinstance(res, dict)
                  and all(k in res for k in result_keys))
            if ok:
                code_cookbook.record_success(
                    agent_role, task_type, tags, signature=error[:300],
                    code=code, result_keys=result_keys, provenance="repaired",
                )
                metrics.agent_repairs_total.labels(agent_name=self.name, outcome="recovered").inc()
                self._log.info("agentic_repair_recovered", run_id=run_id, agent=self.name, attempt=attempt)
                result["agentic_used"] = True
                return result

            last_code = code
            last_err = str(result.get("error", "")) or "RESULT missing required keys"

        metrics.agent_repairs_total.labels(agent_name=self.name, outcome="exhausted").inc()
        self._log.error("agentic_repair_exhausted", run_id=run_id, agent=self.name)
        return {"success": False, "result": None, "stdout": "",
                "error": f"Agentic repair exhausted. Last error: {last_err[:400]}",
                "agentic_used": True}
