from typing import Any

from app.agents.base_agent import BaseAgent
from app.core import mlflow_tracker as mlflow
from app.core.state import AgentState

PROFILING_CODE = '''
import pandas as pd
import numpy as np
import json
import os

# Bounded-memory profiling (P2.3): for very large files, profile a capped SAMPLE
# for the distributional stats while reporting the TRUE row count from a fast
# byte-estimate — so a 10M+ row file never materializes fully in RAM at the audit
# step. Small files are read whole (exact) as before.
_PROFILE_ROW_CAP = 500_000
_true_rows = None
try:
    _sz = os.path.getsize(dataset_path)
    with open(dataset_path, "rb") as _fh:
        _head = _fh.read(min(_sz, 4_000_000))
    _avg = len(_head) / max(_head.count(b"\\n"), 1)
    _est_rows = int(_sz / max(_avg, 1)) - 1
except Exception:
    _est_rows = 0

if _est_rows > _PROFILE_ROW_CAP:
    df = pd.read_csv(dataset_path, nrows=_PROFILE_ROW_CAP)
    _true_rows = _est_rows            # report the real count; profile on the sample
    _profiled_on_sample = True
else:
    df = pd.read_csv(dataset_path)
    _profiled_on_sample = False

# Basic profile — never send raw rows to the agent
null_counts = df.isnull().sum().to_dict()
null_pct = (df.isnull().mean() * 100).round(2).to_dict()

describe_raw = df.describe(include='all').to_dict()
# Convert non-serialisable types
describe_clean = {}
for col, stats in describe_raw.items():
    describe_clean[col] = {k: (None if pd.isna(v) else (float(v) if hasattr(v, 'item') else v))
                           for k, v in stats.items()}

# Target distribution (filled after ProblemFramer sets target_column)
target_col = target_column  # injected into globals by orchestrator
target_dist = {}
imbalance_severity = "none"
if target_col and target_col in df.columns:
    vc = df[target_col].value_counts(normalize=True).round(4)
    target_dist = vc.to_dict()
    # Class imbalance severity: "severe" if minority class < 5% (ratio > 20:1)
    if len(vc) >= 2:
        minority_pct = float(vc.min() * 100)
        if minority_pct < 5.0:
            imbalance_severity = "severe"
        elif minority_pct < 20.0:
            imbalance_severity = "moderate"

# Cardinality
cardinality = df.nunique().to_dict()

# Wrong-Door Guard sensor: detect time-series structure. If a column parses as a
# monotonic-increasing datetime, the rows are time-ordered — running them through
# the tabular pipeline's random K-fold CV yields credible-but-INVALID scores
# (future leaks into training). We flag it so run() can warn the user toward the
# Time-Series studio. Detection only; never blocks.
temporal_signal = {}
try:
    for c in df.columns:
        lc = c.lower()
        if any(tok in lc for tok in ("date", "time", "timestamp", "year", "month")) or c.lower() == "dt":
            parsed = pd.to_datetime(df[c], errors="coerce")
            if parsed.notna().mean() > 0.9 and parsed.is_monotonic_increasing and df.shape[0] > 20:
                temporal_signal = {"column": c, "monotonic": True}
                break
except Exception:
    temporal_signal = {}

# Sample rows (5 only)
sample = df.sample(min(5, len(df)), random_state=42).to_dict(orient='records')

RESULT = {
    # True row count for huge files (profiled on a sample); exact otherwise.
    "shape": [int(_true_rows) if _true_rows is not None else int(df.shape[0]), int(df.shape[1])],
    "profiled_on_sample": _profiled_on_sample,
    "profile_sample_rows": int(df.shape[0]) if _profiled_on_sample else None,
    "dtypes": df.dtypes.astype(str).to_dict(),
    "null_counts": null_counts,
    "null_pct": null_pct,
    "describe": describe_clean,
    "cardinality": cardinality,
    "target_distribution": target_dist,
    "imbalance_severity": imbalance_severity,
    "temporal_signal": temporal_signal,
    "sample_rows": sample,
    "memory_mb": round(df.memory_usage(deep=True).sum() / 1024 / 1024, 2),
}
'''

SYSTEM_PROMPT = """You are a data quality auditor for ML pipelines.
Given a dataset profile (statistics, NOT raw data), assess data quality and decide if the pipeline can proceed.

You MUST respond with valid JSON:
{
  "verdict": "usable" | "warn" | "abort",
  "warnings": ["<list of issues found>"],
  "decisions": [{"decision": "...", "reasoning": "..."}]
}

verdict rules:
- "usable": data looks clean enough, proceed
- "warn": issues found but pipeline can continue (warn user)
- "abort": data is fundamentally broken — ONLY for: fewer than ~30 rows, zero usable
  feature columns, or a completely empty/corrupt file.

CRITICAL: The target column and task type are decided by a LATER step, not here.
They are intentionally blank at audit time. NEVER abort or warn about a "missing
target" or "unspecified task type" — that is expected and not a data problem.
"""


class DataAuditorAgent(BaseAgent):
    name = "data_auditor"

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]
        await self._mark_step(run_id, "running")
        await self.emit(run_id, "Running data quality audit (30-second sanity check)...")

        # Inject target_column as a literal assignment at the top of the code
        # (sandbox globals already have target_column="", this overrides it)
        target_col_value = state.get("target_column") or ""
        code = f'target_column = "{target_col_value}"\n' + PROFILING_CODE

        sandbox_result = await self.execute_code(code, run_id, timeout=60)
        sandbox_result = await self.try_agentic_repair(
            run_id, code, sandbox_result,
            result_keys=["shape", "memory_mb", "null_pct", "cardinality"],
            goal=("Profile the CSV at dataset_path: set RESULT with shape ([rows, cols]), "
                  "memory_mb (float), null_pct ({col: pct}), and cardinality ({col: n_unique})."),
        )
        if not sandbox_result["success"]:
            await self._mark_step(run_id, "failed", sandbox_result["error"])
            return {"error": f"Data profiling failed: {sandbox_result['error']}", "status": "failed"}

        profile = sandbox_result["result"]

        import json
        user_message = f"""
Dataset profile (assess DATA QUALITY only — target & task are chosen later):
- Shape: {profile['shape']}
- Memory: {profile['memory_mb']} MB
- Null percentages (top 10 worst): {json.dumps(dict(sorted(profile['null_pct'].items(), key=lambda x: -x[1])[:10]))}
- Cardinality (top 10): {json.dumps(dict(sorted(profile['cardinality'].items(), key=lambda x: -x[1])[:10]))}
- Sample rows: {json.dumps(profile.get('sample_rows', [])[:3])}

Target column and task type: not yet assigned (a later step decides these — do NOT treat as a problem).
"""
        response = await self.llm.complete_json(SYSTEM_PROMPT, user_message)

        verdict = response.get("verdict", "warn")
        warnings = response.get("warnings", [])

        # Wrong-Door Guard: time-ordered data in the tabular pipeline → warn (never block).
        # If the user is already in the time-series studio, this is expected; stay quiet.
        wrong_door_warning = ""
        ts = profile.get("temporal_signal") or {}
        if ts.get("monotonic") and state.get("pipeline", "tabular") != "timeseries":
            wrong_door_warning = (
                f"Column '{ts.get('column')}' looks time-ordered. The tabular pipeline uses "
                f"random cross-validation, which can OVERSTATE performance on time-series data "
                f"(the future leaks into training). For forecasting/quant tasks, use the "
                f"Time-Series studio (temporal/walk-forward validation)."
            )
            warnings = [wrong_door_warning, *warnings]
            await self.emit(run_id, "⚠ Time-ordered data detected in tabular mode", {"wrong_door": True})

        decision_log_entries = []
        for d in response.get("decisions", []):
            entry = await self._log_decision(
                run_id=run_id,
                decision=d["decision"],
                reasoning=d["reasoning"],
                code_executed=PROFILING_CODE[:200],
                result_summary=f"Shape: {profile['shape']}, Verdict: {verdict}",
            )
            decision_log_entries.append(entry)

        mlflow.log_params({"data_shape_rows": profile["shape"][0], "data_shape_cols": profile["shape"][1]})
        mlflow.set_tag("audit_verdict", verdict)

        # Scale strategy (system-design seam, core/scale.py): decided from measured
        # size, reported so the run explains HOW it will train.
        from app.core.scale import plan_for
        plan = plan_for(state.get("dataset_path", ""))
        scale_entry = await self._log_decision(
            run_id=run_id,
            decision=f"Scale strategy: {plan.strategy}",
            reasoning=plan.reason + (" Ray cluster attached." if plan.ray_available else ""),
            result_summary=f"strategy={plan.strategy}",
        )
        decision_log_entries.append(scale_entry)

        await self.emit(
            run_id,
            f"Audit complete. Verdict: {verdict}. Warnings: {len(warnings)}",
            {"verdict": verdict, "warnings": warnings},
        )

        if verdict == "abort":
            await self._mark_step(run_id, "failed", "; ".join(warnings))
            return {"error": "; ".join(warnings), "status": "failed", "audit_verdict": "abort"}

        await self._mark_step(run_id, "completed")
        existing_log = state.get("decision_log", [])
        existing_cells = state.get("notebook_cells", [])
        new_cell = {
            "agent": self.name,
            "title": "Data Quality Audit",
            "iteration": 0,
            "code": code,
            "stdout": sandbox_result.get("stdout", ""),
            "result_summary": {
                "shape": profile["shape"],
                "memory_mb": profile["memory_mb"],
                "verdict": verdict,
                "warnings": warnings[:5],
                "null_pct_top5": dict(
                    sorted(profile["null_pct"].items(), key=lambda x: -x[1])[:5]
                ),
                "target_distribution": profile.get("target_distribution", {}),
                "imbalance_severity": profile.get("imbalance_severity", "none"),
                "cardinality_top5": dict(
                    sorted(profile["cardinality"].items(), key=lambda x: -x[1])[:5]
                ),
                "dtypes_summary": dict(list(profile["dtypes"].items())[:10]),
            },
        }
        return {
            "data_audit": profile,
            "audit_verdict": verdict,
            "scale_strategy": plan.strategy,
            "imbalance_severity": profile.get("imbalance_severity", "none"),
            "wrong_door_warning": wrong_door_warning,
            "decision_log": existing_log + decision_log_entries,
            "notebook_cells": existing_cells + [new_cell],
        }
