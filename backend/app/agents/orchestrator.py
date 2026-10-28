from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Literal

from langgraph.graph import END, StateGraph
from sqlalchemy import select

from app.agents.baseline_builder import BaselineBuilderAgent
from app.agents.data_auditor import DataAuditorAgent
from app.agents.data_splitter import DataSplitterAgent
from app.agents.eda_agent import EDAAgent
from app.agents.evaluator import EvaluatorAgent
from app.agents.exporter import ExporterAgent
from app.agents.feature_engineer import FeatureEngineerAgent
from app.agents.model_selector import ModelSelectorAgent
from app.agents.preprocessor import PreprocessorAgent
from app.agents.problem_framer import ProblemFramerAgent
from app.agents.tuner import TunerAgent
from app.config import settings
from app.core import metrics
from app.core import mlflow_tracker as mlflow
from app.core.logging import get_logger
from app.core.state import AgentState

_log = get_logger("orchestrator")
from app.database import AsyncSessionLocal
from app.models.run import Run
from app.redis_client import publish_progress, set_run_state


# ── Agent singletons ──────────────────────────────────────────────────────────
_problem_framer = ProblemFramerAgent()
_data_auditor = DataAuditorAgent()
_data_splitter = DataSplitterAgent()
_baseline_builder = BaselineBuilderAgent()
_eda_agent = EDAAgent()
_preprocessor = PreprocessorAgent()
_feature_engineer = FeatureEngineerAgent()
_model_selector = ModelSelectorAgent()
_tuner = TunerAgent()
_evaluator = EvaluatorAgent()
_exporter = ExporterAgent()


# ── Node wrappers (LangGraph expects sync OR async callables) ─────────────────
async def _safe_run(agent, state: AgentState) -> dict:
    """Safety net: any UNHANDLED agent exception (e.g. a KeyError on a RESULT key
    the sandbox self-repair couldn't see) becomes a clean failed status instead of
    hard-crashing the pipeline — so the graph's fail-fast / diagnostic back-jump
    routing handles it (and can even recover) rather than the whole run dying."""
    import asyncio
    from app.core.tracing import span
    from app.core import context as run_context
    name = getattr(agent, "name", "agent")
    budget = int(getattr(settings, "MAX_AGENT_SECONDS", 0) or 0)
    # Bind the run's CURRENT dataset path (train.csv after the split) so every
    # sandbox execution — template, repair, or agentic — reads train-only data.
    run_context.set_dataset_path(state.get("dataset_path"))
    try:
        with span(f"agent.{name}", run_id=state.get("run_id"), agent=name):
            if budget > 0:
                return await asyncio.wait_for(agent.run(state), timeout=budget)
            return await agent.run(state)
    except (asyncio.TimeoutError, TimeoutError):
        _log.error("agent_timeout", agent=name, run_id=state.get("run_id"), budget_s=budget)
        return {"status": "failed",
                "error": f"{name} exceeded its {budget}s wall-clock budget (MAX_AGENT_SECONDS)"}
    except Exception as e:  # noqa: BLE001 — deliberate catch-all at the node boundary
        _log.error("agent_crashed", agent=name, run_id=state.get("run_id"),
                   error=f"{type(e).__name__}: {str(e)[:200]}")
        return {"status": "failed", "error": f"{name} crashed: {type(e).__name__}: {str(e)[:200]}"}


async def _checkpoint(node_name: str, state: AgentState, partial: dict) -> None:
    """P1.8 — persist the merged state after a SUCCESSFUL node so Retry can
    resume from the next failure point. Failed partials are never checkpointed
    (the last good checkpoint is the resume base). Never breaks a run."""
    if partial.get("status") == "failed":
        return
    try:
        from app.redis_client import set_checkpoint
        await set_checkpoint(state["run_id"], node_name, {**dict(state), **partial})
    except Exception as e:
        _log.warning("checkpoint_failed", run_id=state.get("run_id"),
                     node=node_name, error=str(e)[:150])


def _make_node(agent_attr: str, node_name: str):
    """The agent is resolved from module globals at CALL time so tests (and any
    future hot-swap) that monkeypatch the `_agent` singletons keep working."""
    async def node(state: AgentState) -> dict:
        partial = await _safe_run(globals()[agent_attr], state)
        await _checkpoint(node_name, state, partial)
        return partial
    return node


node_data_auditor = _make_node("_data_auditor", "data_auditor")
node_problem_framer = _make_node("_problem_framer", "problem_framer")
node_data_splitter = _make_node("_data_splitter", "data_splitter")
node_baseline = _make_node("_baseline_builder", "baseline_builder")
node_eda = _make_node("_eda_agent", "eda_agent")
node_preprocessor = _make_node("_preprocessor", "preprocessor")
node_feature_engineer = _make_node("_feature_engineer", "feature_engineer")
node_model_selector = _make_node("_model_selector", "model_selector")
node_tuner = _make_node("_tuner", "tuner")


async def node_evaluator(state: AgentState) -> dict:
    # Iteration-gate comparison basis (fixed 2026-07-03): compare HOLDOUT to
    # HOLDOUT across iterations. The old code captured state["current_score"]
    # as prev — but the tuner overwrites current_score with its CV score, so
    # the gate measured |holdout − tuned-CV| (the CV/holdout gap, ~constant),
    # saw "improvement" every round, and always looped to the iteration cap
    # (observed live: 3 identical iterations on the 1M run).
    prev_holdout = state.get("last_holdout_score")
    result = await _safe_run(_evaluator, state)
    if result.get("status") != "failed":
        if prev_holdout is not None:
            result["prev_score"] = prev_holdout
        if result.get("current_score") is not None:
            result["last_holdout_score"] = result["current_score"]
        result["iteration"] = state.get("iteration", 0) + 1
    await _checkpoint("evaluator", state, result)
    return result


node_exporter = _make_node("_exporter", "exporter")


async def node_trust_gate(state: AgentState) -> dict:
    """Inline Skeptic — the brain moved into the pipeline. Before declaring success,
    run the trust probes; if the result is untrustworthy due to leakage, auto-launch
    a corrected re-run without the leaked columns. Fully defensive: any failure here
    degrades to a no-op so it can never break a run."""
    run_id = state.get("run_id")
    try:
        enriched = state.get("enriched_data_path")
        model_path = state.get("tuned_model_path") or state.get("winner_model_path")
        if not enriched or not model_path:
            return {}
        from app.agents.trust_auditor import TrustAuditor, gate_decision
        report = await TrustAuditor().audit_from(
            run_id, enriched, model_path, state.get("task_type"), state.get("primary_metric"),
            state.get("baseline_score"), state.get("current_score", state.get("final_score")),
            state.get("score_std", 0.0), state.get("evaluation_basis", "holdout"),
        )
        decision = gate_decision(report)
        _log.info("trust_gate", run_id=run_id, grade=report.get("grade"),
                  untrustworthy=decision["untrustworthy"], leaked=len(decision["leaked_columns"]))

        # Phase 3 — cross-run memory: every gated run contributes its outcome
        # (grade + winner); leakage columns are added below once raw-mapped.
        from app.core import run_memory
        mem_sig = run_memory.signature_for_run(run_id, state.get("dataset_path", ""))

        if not decision["untrustworthy"] or not decision["leaked_columns"]:
            run_memory.record_run_outcome(
                mem_sig, run_id, grade=report.get("grade"),
                winner_model=state.get("winner_model"),
                final_score=state.get("current_score", state.get("final_score")),
            )
            return {}

        from app.redis_client import get_autocorrect_origin
        try:
            already_corrected = bool(await get_autocorrect_origin(run_id))
        except Exception:
            already_corrected = False
        # The probe reports POST-preprocessing names (cat_onehot__ioc_type_domain,
        # num__redshift); exclude_columns operates on RAW columns, so map back to
        # the raw column or the re-run would exclude nothing and re-leak.
        raw_cols: list[str] = []
        try:
            with open(state.get("dataset_path", ""), "r", encoding="utf-8", errors="ignore") as fh:
                raw_cols = [c.strip() for c in fh.readline().strip().split(",")]
        except OSError:
            pass

        # Exact map first (written by the preprocessor from the fitted
        # ColumnTransformer — immune to underscores in category values);
        # prefix heuristic only as fallback for engineered/unknown names.
        source_map: dict = {}
        try:
            fsm_path = state.get("feature_source_map_path") or ""
            if fsm_path and os.path.exists(fsm_path):
                import json as _json
                with open(fsm_path, "r", encoding="utf-8") as fh:
                    loaded = _json.load(fh)
                if isinstance(loaded, dict):
                    source_map = loaded
        except Exception:
            source_map = {}

        def _raw_of(feat: str) -> str | None:
            exact = source_map.get(feat)
            if isinstance(exact, str) and exact:
                return exact
            for c in raw_cols:
                if not c:
                    continue
                if (feat == c or feat.startswith(f"cat_onehot__{c}_") or feat.startswith(f"cat__{c}_")
                        or feat.startswith(f"num__{c}") or feat.endswith(f"__{c}")):
                    return c
            return None

        target = state.get("target_column")
        mapped: list[str] = []
        for f in decision["leaked_columns"]:
            c = _raw_of(f)
            if c and c != target and c not in mapped:
                mapped.append(c)
        leaked_raw = mapped or decision["leaked_columns"]   # fall back to raw names

        run_memory.record_run_outcome(
            mem_sig, run_id, grade=report.get("grade"),
            leaked_columns=mapped,
            winner_model=state.get("winner_model"),
            final_score=state.get("current_score", state.get("final_score")),
        )

        cur_excl = state.get("exclude_columns") or []
        new_leaks = [c for c in leaked_raw if c not in cur_excl]
        if already_corrected or not new_leaks or len(cur_excl) >= 25:
            return {}

        from app.core.rerun import clone_and_submit
        new_id = await clone_and_submit(run_id, new_leaks, mark_autocorrect=True)
        if new_id:
            await publish_progress(run_id, {
                "agent": "trust_gate",
                "message": f"Leakage detected — auto-launched a corrected re-run without "
                           f"{len(new_leaks)} feature(s): {new_id[:8]}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": {"autocorrection_run_id": new_id, "excluded": new_leaks},
            })
        return {}
    except Exception as e:
        _log.warning("trust_gate_error", run_id=run_id, error=str(e)[:200])
        return {}


# ── Routing logic ─────────────────────────────────────────────────────────────
def route_after_audit(state: AgentState) -> Literal["problem_framer", END]:
    if state.get("status") == "failed" or state.get("audit_verdict") == "abort":
        return END
    return "problem_framer"


def route_after_baseline(state: AgentState) -> Literal["eda", END]:
    if state.get("status") == "failed":
        return END
    return "eda"


def route_after_evaluator(state: AgentState) -> Literal["feature_engineer", "trust_gate", "exporter"]:
    if state.get("status") == "failed":
        return "exporter"

    iteration = state.get("iteration", 0)
    max_iter = state.get("max_iterations", settings.MAX_ITERATIONS)
    current = state.get("current_score", 0.0)
    prev = state.get("prev_score", 0.0)
    # DIRECTIONAL gain (2026-07-04 fix). This was abs(current - prev), which made a
    # score that got WORSE by more than the threshold look like progress — the loop
    # then burned its whole budget chasing a regression. `improvement` must mean
    # "improvement", so lower-is-better metrics (rmse/mae/log_loss) invert.
    from app.core import metric_registry as _mreg
    _metric = state.get("primary_metric", "")
    improvement = (prev - current) if not _mreg.higher_is_better(_metric) else (current - prev)

    # Significance gate: only iterate if the change clears BOTH the configured
    # threshold AND the run's measured noise floor (CV std of the score). A gain
    # smaller than fold-to-fold noise is not real improvement — chasing it just
    # overfits the evaluation. (Phase 0.2)
    noise_floor = float(state.get("score_std", 0.0) or 0.0)
    min_delta = max(settings.IMPROVEMENT_THRESHOLD, noise_floor)

    # P1.7: the improvement loop runs THROUGH feature_engineer, so a plan that
    # skipped it also disables iteration (there is nothing to re-tune into).
    if (state.get("pipeline_plan") or {}).get("skip_feature_engineer"):
        return "trust_gate"
    should_iterate = iteration < max_iter and improvement >= min_delta
    return "feature_engineer" if should_iterate else "trust_gate"


def _make_failfast_router(next_node: str):
    """Stop the pipeline immediately when an agent reports failure.

    Without this, a failed agent's state flows into the next agent and the
    pipeline runs as a zombie until something crashes on missing data.
    """
    def router(state: AgentState) -> str:
        if state.get("status") == "failed":
            return END
        return next_node
    return router


def _make_diagnostic_router(next_node: str):
    """Tier-2 (P8): on failure, jump back ONCE to re-examine the data (diagnostic →
    eda) instead of ending. After one back-jump the run fails fast as before.
    P1.7: honors the plan's skip_feature_engineer (preprocessor → model_selector)."""
    def router(state: AgentState) -> str:
        if state.get("status") == "failed":
            return "diagnostic" if state.get("backjumps_used", 0) < 1 else END
        plan = state.get("pipeline_plan") or {}
        if next_node == "feature_engineer" and plan.get("skip_feature_engineer"):
            return "model_selector"
        return next_node
    return router


def route_after_model_selector(state: AgentState) -> str:
    """P1.7: skip the tuner when the plan says so (tiny data → tuning is noise)."""
    if state.get("status") == "failed":
        return END
    if (state.get("pipeline_plan") or {}).get("skip_tuner"):
        return "evaluator"
    return "tuner"


def route_after_feature_engineer(state: AgentState) -> str:
    """Fail-fast with a SALVAGE path: if feature engineering fails mid-iteration
    (e.g. an LLM outage on iteration 2+) but a scored model already exists from a
    previous iteration, stop iterating and export what we have instead of failing
    a run that has a perfectly good result."""
    if state.get("status") == "failed":
        if state.get("current_score") is not None and (
                state.get("tuned_model_path") or state.get("winner_model_path")):
            return "salvage"
        return END
    return "model_selector"


async def node_salvage(state: AgentState) -> dict:
    """Clear the failure and close out with the best model so far."""
    err = str(state.get("error", ""))[:200]
    _log.info("salvage_early_export", run_id=state.get("run_id"), error=err[:120])
    await publish_progress(state.get("run_id"), {
        "agent": "orchestrator",
        "message": f"Iteration stopped early ({err[:90]}…) — exporting the best model so far.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return {"status": "running", "error": None}


async def _propose_plan(state: AgentState) -> dict:
    """LLM plan proposal, isolated as a patchable seam (tests stub this to the
    default plan; production calls the Strategist)."""
    from app.core import planner as planner_mod
    from app.core.llm import get_llm
    default_max = settings.MAX_ITERATIONS
    audit = state.get("data_audit") or {}
    shape = audit.get("shape") or [0, 0]
    dossier = (
        f"Rows: {shape[0]}, Columns: {shape[1]}. Task: {state.get('task_type')}. "
        f"Target: {state.get('target_column')}. "
        f"High-null columns: {list((audit.get('null_pct') or {}).keys())[:6]}. "
        f"Scale strategy: {state.get('scale_strategy', 'in_memory')}."
    )
    system = (
        "You plan an AutoML run. You may ONLY choose: skip_feature_engineer (bool), "
        "skip_tuner (bool), max_iterations (int 0-3), and a one-line rationale. "
        "Guidance: skip feature engineering when the dataset is tiny (<200 rows) or already "
        "very wide (>300 columns); skip tuning on tiny data (tuning is noise); use fewer "
        "iterations for small/clean data. EDA, preprocessing, model selection and evaluation "
        "always run. Respond as JSON: "
        '{"skip_feature_engineer":false,"skip_tuner":false,"max_iterations":3,"rationale":"..."}'
    )
    obj = await get_llm().complete_json(system, dossier)
    return planner_mod.validate_plan(obj, default_max)


async def node_planner(state: AgentState) -> dict:
    """P1.7 v1 — the Strategist plans THIS run: justified skips (feature
    engineering / tuning) + a per-dataset iteration budget, on the existing static
    graph. Fully defensive: any failure → the full fixed pipeline (default plan)."""
    from app.core import planner as planner_mod
    run_id = state.get("run_id")
    default_max = settings.MAX_ITERATIONS
    try:
        plan = await _propose_plan(state)
    except Exception as e:
        _log.warning("planner_failed_default", run_id=run_id, error=str(e)[:150])
        plan = planner_mod.default_plan(default_max)

    entry = None
    try:
        entry = await _planner_log(run_id, plan, planner_mod.plan_summary(plan))
    except Exception:
        pass
    await publish_progress(run_id, {
        "agent": "planner",
        "message": f"Pipeline plan: {planner_mod.plan_summary(plan)}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    out = {"pipeline_plan": plan, "max_iterations": plan["max_iterations"]}
    if entry:
        out["decision_log"] = state.get("decision_log", []) + [entry]
    return out


async def _planner_log(run_id, plan, summary):
    """Write the plan as a decision-log row (mirrors BaseAgent._log_decision without
    an agent instance)."""
    from datetime import datetime as _dt, timezone as _tz
    from app.database import AsyncSessionLocal
    from app.models.run import DecisionLog
    entry = {
        "agent": "planner", "decision": f"Pipeline plan: {summary}",
        "reasoning": plan.get("rationale", ""), "timestamp": _dt.now(_tz.utc).isoformat(),
        "result_summary": f"skip_fe={plan['skip_feature_engineer']}, skip_tuner={plan['skip_tuner']}, "
                          f"max_iter={plan['max_iterations']}",
    }
    async with AsyncSessionLocal() as db:
        db.add(DecisionLog(run_id=run_id, agent_name="planner",
                           decision=entry["decision"], reasoning=entry["reasoning"],
                           result_summary=entry["result_summary"]))
        await db.commit()
    return entry


async def node_diagnostic(state: AgentState) -> dict:
    """Clear the failed status, record a hint from the error, and let the run
    re-enter EDA → preprocessor once. Bounded by backjumps_used."""
    err = str(state.get("error", ""))[:300]
    _log.info("diagnostic_backjump", run_id=state.get("run_id"), error=err[:120])
    return {
        "status": "running",
        "error": None,
        "backjumps_used": state.get("backjumps_used", 0) + 1,
        "repair_hint": f"A prior step failed: {err}. Re-examine column types / encodings and adjust.",
    }


# All resumable entry nodes (P1.8) — a failed AgentStep's agent name maps 1:1.
RESUMABLE_NODES = {
    "data_auditor", "problem_framer", "data_splitter", "baseline_builder",
    "eda_agent", "preprocessor", "feature_engineer", "model_selector",
    "tuner", "evaluator", "exporter",
}


# ── Build the graph ───────────────────────────────────────────────────────────
def build_graph(entry_point: str = "data_auditor") -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("data_auditor", node_data_auditor)
    graph.add_node("problem_framer", node_problem_framer)
    graph.add_node("data_splitter", node_data_splitter)
    graph.add_node("baseline_builder", node_baseline)
    graph.add_node("eda_agent", node_eda)
    graph.add_node("preprocessor", node_preprocessor)
    graph.add_node("feature_engineer", node_feature_engineer)
    graph.add_node("model_selector", node_model_selector)
    graph.add_node("tuner", node_tuner)
    graph.add_node("evaluator", node_evaluator)
    graph.add_node("trust_gate", node_trust_gate)
    graph.add_node("exporter", node_exporter)
    graph.add_node("diagnostic", node_diagnostic)
    graph.add_node("salvage", node_salvage)
    graph.add_node("planner", node_planner)

    graph.set_entry_point(entry_point if entry_point in RESUMABLE_NODES else "data_auditor")

    graph.add_conditional_edges(
        "data_auditor",
        route_after_audit,
        {"problem_framer": "problem_framer", END: END},
    )
    graph.add_edge("problem_framer", "data_splitter")
    # P1.7: the planner runs after the split (target + shape known) and before any
    # fitting, so its skip/iteration decisions apply to the whole run.
    graph.add_edge("data_splitter", "planner")
    graph.add_edge("planner", "baseline_builder")
    graph.add_conditional_edges(
        "baseline_builder",
        route_after_baseline,
        {"eda": "eda_agent", END: END},
    )
    # Fail-fast conditional edges: a failed agent ends the run cleanly
    # instead of cascading bad state into downstream agents.
    graph.add_conditional_edges(
        "eda_agent", _make_failfast_router("preprocessor"),
        {"preprocessor": "preprocessor", END: END},
    )
    # Preprocessor uses the Tier-2 diagnostic router: a failure (usually a data-type
    # surprise) jumps back to EDA once to re-examine, then fails fast.
    graph.add_conditional_edges(
        "preprocessor", _make_diagnostic_router("feature_engineer"),
        {"feature_engineer": "feature_engineer", "model_selector": "model_selector",
         "diagnostic": "diagnostic", END: END},
    )
    graph.add_edge("diagnostic", "eda_agent")
    graph.add_conditional_edges(
        "feature_engineer", route_after_feature_engineer,
        {"model_selector": "model_selector", "salvage": "salvage", END: END},
    )
    graph.add_edge("salvage", "trust_gate")
    graph.add_conditional_edges(
        "model_selector", route_after_model_selector,
        {"tuner": "tuner", "evaluator": "evaluator", END: END},
    )
    graph.add_conditional_edges(
        "tuner", _make_failfast_router("evaluator"),
        {"evaluator": "evaluator", END: END},
    )
    graph.add_conditional_edges(
        "evaluator",
        route_after_evaluator,
        {"feature_engineer": "feature_engineer", "trust_gate": "trust_gate", "exporter": "exporter"},
    )
    graph.add_edge("trust_gate", "exporter")
    graph.add_edge("exporter", END)

    return graph.compile()


_compiled_graph = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


# ── Entry point called by the API ─────────────────────────────────────────────
async def run_pipeline(run_id: str, resume_from: str | None = None) -> None:
    try:
        await _run_pipeline_inner(run_id, resume_from)
    except Exception as exc:
        # Top-level zombie net: ANY crash before/around the graph (e.g. the old
        # fluent-MLflow "already active" collision) must leave a clean failed
        # run, never an eternally-"running" one.
        _log.error("pipeline_setup_crashed", run_id=run_id,
                   error=f"{type(exc).__name__}: {str(exc)[:300]}")
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Run).where(Run.id == run_id))
                run = result.scalar_one_or_none()
                if run and run.status in ("queued", "running"):
                    run.status = "failed"
                    run.error_message = (f"Pipeline setup crashed: "
                                         f"{type(exc).__name__}: {str(exc)[:300]}")
                    await db.commit()
            await publish_progress(run_id, {
                "agent": "orchestrator",
                "message": "Pipeline failed during setup — click Retry.",
                "status": "failed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            pass
        raise


async def _run_pipeline_inner(run_id: str, resume_from: str | None = None) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Run).where(Run.id == run_id))
        run = result.scalar_one_or_none()
        if not run:
            return

        run.status = "running"
        # S3 seam: an s3:// dataset is fetched to the local /data volume once, so
        # the rest of the pipeline reads a local CSV unchanged.
        try:
            from app.core.cloud import is_remote, localize
            if is_remote(run.dataset_path):
                run.dataset_path = localize(run.dataset_path, run_id)
        except Exception as exc:
            run.status = "failed"
            run.error_message = f"Could not load dataset: {str(exc)[:200]}"
            await db.commit()
            return
        # Robust ingestion: normalize delimiter/encoding ONCE so every downstream
        # template (which all assume comma/UTF-8) works. Idempotent — clean files
        # are untouched. (Fixes the ;-delimited bank+marketing failure.)
        try:
            from app.core.ingest import normalize_csv
            rep = normalize_csv(run.dataset_path)
            if rep.get("changed"):
                await publish_progress(run_id, {
                    "agent": "orchestrator",
                    "message": f"Normalized dataset: detected '{rep['delimiter']}' delimiter"
                               f"{'' if rep['encoding'] == 'utf-8' else ', ' + rep['encoding'] + ' encoding'}"
                               f" → standard CSV ({rep.get('n_cols')} columns).",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        except Exception as exc:
            _log.warning("ingest_normalize_skipped", run_id=run_id, error=str(exc)[:150])
        await db.commit()

    mlflow.setup_mlflow()
    mlflow_run_id = mlflow.start_run(
        run_id=run_id,
        tags={"status": "running"},
    )

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Run).where(Run.id == run_id))
        run = result.scalar_one_or_none()
        if run:
            run.mlflow_run_id = mlflow_run_id
            await db.commit()

    initial_state: AgentState = {
        "run_id": run_id,
        "mlflow_run_id": mlflow_run_id,
        "dataset_path": run.dataset_path,
        "user_goal": run.user_goal,
        "exclude_columns": run.exclude_columns or [],
        "fp_fn_preference": run.fp_fn_preference or "",
        "interpretability_required": run.interpretability_required,
        "pipeline": getattr(run, "pipeline", "tabular") or "tabular",
        "decision_log": [],
        "notebook_cells": [],      # each agent appends its executed code + results
        "iteration": 0,
        "max_iterations": settings.MAX_ITERATIONS,
        "iteration_scores": [],
        "status": "running",
        "data_dir": settings.DATA_DIR,
    }

    pipeline_start = datetime.now(timezone.utc)
    metrics.pipeline_runs_total.labels(status="started").inc()
    metrics.active_pipelines.inc()
    _log.info("pipeline_started", run_id=run_id)

    await publish_progress(run_id, {
        "agent": "orchestrator",
        "message": "Pipeline started",
        "timestamp": pipeline_start.isoformat(),
    })

    # P1.8 — resume from the failed node: rebuild the initial state from the
    # last good checkpoint and enter the graph at the failed node. Any missing
    # piece (no checkpoint, unknown node, TS pipeline) falls back to a full run.
    resume_entry: str | None = None
    if (resume_from and resume_from in RESUMABLE_NODES
            and initial_state.get("pipeline") != "timeseries"):
        try:
            from app.redis_client import get_checkpoint
            ckpt = await get_checkpoint(run_id)
            if ckpt and isinstance(ckpt.get("state"), dict):
                restored = dict(ckpt["state"])
                restored.update({
                    "run_id": run_id,
                    "mlflow_run_id": mlflow_run_id,
                    "status": "running",
                    "error": None,
                })
                initial_state = restored  # type: ignore[assignment]
                resume_entry = resume_from
                _log.info("pipeline_resume", run_id=run_id,
                          entry=resume_from, checkpoint_node=ckpt.get("node"))
                await publish_progress(run_id, {
                    "agent": "orchestrator",
                    "message": f"Resuming from failed step: {resume_from} "
                               f"(checkpoint after {ckpt.get('node')})",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        except Exception as e:
            _log.warning("pipeline_resume_failed_full_restart", run_id=run_id, error=str(e)[:200])

    try:
        # Platform seam: the studio registry owns graph selection (adding a
        # studio = one registration, not another branch here).
        from app.core.studios import get_studio
        studio = get_studio(initial_state.get("pipeline"))
        if resume_entry and studio.supports_resume:
            graph = studio.graph_builder(entry_point=resume_entry)
        else:
            graph = studio.graph_builder()
        # R6: optional hard wall-clock budget for the whole run.
        from app.core.tracing import span as _span
        with _span("pipeline.run", run_id=run_id, pipeline=initial_state.get("pipeline")):
            if settings.MAX_RUN_SECONDS and settings.MAX_RUN_SECONDS > 0:
                import asyncio
                final_state: AgentState = await asyncio.wait_for(
                    graph.ainvoke(initial_state), timeout=settings.MAX_RUN_SECONDS
                )
            else:
                final_state = await graph.ainvoke(initial_state)

        status = "completed" if final_state.get("status") != "failed" else "failed"
        elapsed = (datetime.now(timezone.utc) - pipeline_start).total_seconds()
        metrics.pipeline_runs_total.labels(status=status).inc()
        metrics.pipeline_duration_seconds.observe(elapsed)
        metrics.active_pipelines.dec()

        if final_state.get("baseline_score"):
            metrics.pipeline_baseline_score.observe(final_state["baseline_score"])
        if final_state.get("current_score"):
            metrics.pipeline_final_score.observe(final_state["current_score"])
        if final_state.get("baseline_score") and final_state.get("current_score"):
            improvement = final_state["current_score"] - final_state["baseline_score"]
            metrics.model_score_improvement.observe(improvement)
        if final_state.get("features_created") is not None:
            metrics.features_kept_total.observe(len(final_state.get("features_created", [])))

        _log.info("pipeline_finished", run_id=run_id, status=status, duration_s=round(elapsed, 1))

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Run).where(Run.id == run_id))
            run = result.scalar_one_or_none()
            if run:
                run.status = status
                run.task_type = final_state.get("task_type")
                run.target_column = final_state.get("target_column")
                run.primary_metric = final_state.get("primary_metric")
                run.baseline_score = final_state.get("baseline_score")
                run.final_score = final_state.get("current_score")
                run.winner_model = final_state.get("winner_model")
                run.iteration_count = final_state.get("iteration", 0)
                run.error_message = final_state.get("error")
                await db.commit()

        await set_run_state(run_id, dict(final_state))

        mlflow.log_metric("final_score", final_state.get("current_score") or 0)
        mlflow.set_tag("status", status)
        mlflow.set_tag("winner_model", final_state.get("winner_model") or "")
        mlflow.end_run("FINISHED" if status == "completed" else "FAILED")

        await publish_progress(run_id, {
            "agent": "orchestrator",
            "message": f"Pipeline {status}",
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    except Exception as exc:
        if isinstance(exc, (TimeoutError,)):
            error_msg = (f"Run exceeded its {settings.MAX_RUN_SECONDS}s wall-clock "
                         f"budget (MAX_RUN_SECONDS)")
        else:
            error_msg = str(exc) or type(exc).__name__
        metrics.pipeline_runs_total.labels(status="failed").inc()
        metrics.active_pipelines.dec()
        _log.error("pipeline_exception", run_id=run_id, error=error_msg)

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Run).where(Run.id == run_id))
            run = result.scalar_one_or_none()
            if run:
                run.status = "failed"
                run.error_message = error_msg
                await db.commit()

            # Close out any step left "running" — an exception that escapes an
            # agent (e.g. LLM outage) never reaches its own _mark_step("failed").
            from app.models.run import AgentStep
            steps_q = await db.execute(
                select(AgentStep).where(
                    AgentStep.run_id == run_id, AgentStep.status == "running"
                )
            )
            for step in steps_q.scalars().all():
                step.status = "failed"
                step.completed_at = datetime.now(timezone.utc)
                step.error_message = error_msg[:500]
            await db.commit()

        mlflow.set_tag("status", "failed")
        mlflow.end_run("FAILED")

        await publish_progress(run_id, {
            "agent": "orchestrator",
            "message": f"Pipeline failed: {error_msg}",
            "status": "failed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        raise
