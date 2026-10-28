"""Phase 0.2 — honest evaluation: carve a true holdout up front.

Runs right after problem framing (target/task known) and BEFORE any agent
fits, selects features, picks a model, or tunes. It physically splits the raw
dataset into ``train.csv`` (the working set every downstream agent reads) and
``holdout.csv`` (never seen until the evaluator scores it). Because every
downstream agent reads ``dataset_path``, simply repointing it at ``train.csv``
makes the entire pipeline train-only with no template changes — eliminating the
selection/tuning leakage that made the old "holdout" inside the evaluator
optimistic.

Doctrine: this is a *mechanic/rail* (how data is partitioned), not an ML
decision — so it lives in code. Time-series uses its own graph + temporal split
and never reaches this node.
"""

from typing import Any

from app.agents.base_agent import BaseAgent
from app.core.state import AgentState

# Below this many rows a 20% holdout is too small to be meaningful — fall back
# to CV-only evaluation (evaluator handles holdout_path == "").
_MIN_ROWS_FOR_HOLDOUT = 60

SPLIT_CODE_TEMPLATE = '''
import pandas as pd
import os
from sklearn.model_selection import train_test_split

df = pd.read_csv(dataset_path)
target_col = __TARGET_COLUMN__
task_type = __TASK_TYPE__
holdout_frac = __HOLDOUT_FRAC__
min_rows = __MIN_ROWS__
max_train_rows = __MAX_TRAIN_ROWS__
max_hard_rows = __MAX_HARD_ROWS__
keep_full = __KEEP_FULL__   # streaming strategy: out-of-core learners need every row

run_dir = os.path.dirname(artifacts_dir.rstrip("/"))  # /data/<run_id>
train_path = os.path.join(run_dir, "train.csv")
holdout_path = os.path.join(run_dir, "holdout.csv")

n = len(df)
if n < min_rows:
    # Too small for a trustworthy holdout — keep all rows for training, signal
    # the evaluator to fall back to CV.
    RESULT = {"split": False, "n_total": int(n), "reason": "dataset too small for a holdout"}
else:
    # Stratify only for single-label classification with a clean, multi-class
    # target where every class can appear on both sides.
    stratify = None
    if task_type in ("binary_classification", "multiclass_classification") and target_col in df.columns:
        yv = df[target_col]
        vc = yv.value_counts(dropna=True)
        if len(vc) > 1 and int(vc.min()) >= 2 and bool(yv.notna().all()):
            stratify = yv
    try:
        train_df, holdout_df = train_test_split(
            df, test_size=holdout_frac, random_state=42, stratify=stratify
        )
    except Exception:
        train_df, holdout_df = train_test_split(
            df, test_size=holdout_frac, random_state=42
        )
    # Global training-row cap: sample the TRAIN side down so every downstream
    # agent trains on a bounded set. The holdout is never sampled.
    n_train_full = int(len(train_df))
    capped = False
    learning_curve = {}
    cap_escalated = False

    def _cap_sample(df_in, n_rows):
        cs = None
        if stratify is not None:
            yv2 = df_in[target_col]
            if int(yv2.value_counts(dropna=True).min()) >= 2:
                cs = yv2
        try:
            out, _ = train_test_split(df_in, train_size=n_rows, random_state=42, stratify=cs)
            return out
        except Exception:
            return df_in.sample(n=n_rows, random_state=42)

    if max_train_rows and n_train_full > max_train_rows:
        train_df_full = train_df
        train_df = _cap_sample(train_df_full, max_train_rows)
        capped = True

        # S1 — learning-curve proof: is the cap actually free, or is the model
        # still learning at the cap? Fast probe (HistGB, quick label-encode) on
        # 25/50/100% of the capped sample, scored on a fixed validation slice.
        try:
            import numpy as np
            from sklearn.ensemble import (HistGradientBoostingClassifier,
                                          HistGradientBoostingRegressor)
            from sklearn.metrics import accuracy_score, r2_score

            probe = train_df
            enc = pd.DataFrame(index=probe.index)
            for c in probe.columns:
                if c == target_col:
                    continue
                col = probe[c]
                if pd.api.types.is_numeric_dtype(col):
                    enc[c] = pd.to_numeric(col, errors="coerce")
                elif col.nunique(dropna=True) <= 1000:
                    enc[c] = pd.factorize(col.astype(str))[0]
            yv = probe[target_col]
            is_reg = task_type == "regression"
            yv = pd.to_numeric(yv, errors="coerce") if is_reg else pd.Series(pd.factorize(yv.astype(str))[0], index=probe.index)
            ok = yv.notna()
            enc, yv = enc[ok], yv[ok]

            n = len(enc)
            if n < 20000:
                raise ValueError("too few rows for a meaningful learning-curve probe")
            n_val = max(2000, int(n * 0.1))
            Xv, yv_val = enc.iloc[-n_val:], yv.iloc[-n_val:]
            Xp, yp = enc.iloc[:-n_val], yv.iloc[:-n_val]
            Model = HistGradientBoostingRegressor if is_reg else HistGradientBoostingClassifier
            for frac in (0.25, 0.5, 1.0):
                k = max(1000, int(len(Xp) * frac))
                m = Model(max_iter=60, random_state=42)
                m.fit(Xp.iloc[:k], yp.iloc[:k])
                pred = m.predict(Xv)
                s = r2_score(yv_val, pred) if is_reg else accuracy_score(yv_val, pred)
                learning_curve[str(k)] = round(float(s), 5)

            scores = list(learning_curve.values())
            still_climbing = (scores[-1] - scores[-2]) > 0.005 and (scores[-2] - scores[-3]) > 0.005
            if still_climbing and max_hard_rows and n_train_full > max_train_rows:
                new_cap = min(max_hard_rows, n_train_full)
                if new_cap > max_train_rows:
                    train_df = _cap_sample(train_df_full, new_cap)
                    cap_escalated = True
        except Exception as _lc_err:
            learning_curve = {"error": str(_lc_err)[:150]}
    # Streaming: keep the UNCAPPED train side for out-of-core learners (chunked
    # partial_fit reads it; in-memory candidates still use the capped train.csv).
    train_full_path = ""
    if keep_full and capped:
        train_full_path = os.path.join(run_dir, "train_full.csv")
        train_df_full.to_csv(train_full_path, index=False)

    train_df.to_csv(train_path, index=False)
    holdout_df.to_csv(holdout_path, index=False)
    RESULT = {
        "split": True,
        "train_path": train_path,
        "holdout_path": holdout_path,
        "n_train": int(len(train_df)),
        "n_train_full": n_train_full,
        "n_holdout": int(len(holdout_df)),
        "stratified": stratify is not None,
        "train_capped": capped,
        "learning_curve": learning_curve,
        "cap_escalated": cap_escalated,
        "train_full_path": train_full_path,
    }
'''


class DataSplitterAgent(BaseAgent):
    name = "data_splitter"

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]
        await self._mark_step(run_id, "running")
        await self.emit(run_id, "Reserving a held-out test set before any fitting — honest evaluation...")

        holdout_frac = float(state.get("holdout_frac") or 0.2)
        from app.config import settings
        max_train_rows = max(0, int(getattr(settings, "MAX_TRAIN_ROWS", 0) or 0))
        max_hard_rows = max(0, int(getattr(settings, "MAX_TRAIN_ROWS_HARD", 0) or 0))
        code = (
            SPLIT_CODE_TEMPLATE
            .replace("__TARGET_COLUMN__", repr(state.get("target_column", "")))
            .replace("__TASK_TYPE__", repr(state.get("task_type", "")))
            .replace("__HOLDOUT_FRAC__", repr(holdout_frac))
            .replace("__MIN_ROWS__", repr(_MIN_ROWS_FOR_HOLDOUT))
            .replace("__MAX_TRAIN_ROWS__", repr(max_train_rows))
            .replace("__MAX_HARD_ROWS__", repr(max_hard_rows))
            .replace("__KEEP_FULL__", repr(state.get("scale_strategy") == "streaming"))
        )

        result = await self.execute_code(code, run_id, timeout=120)
        if not result["success"]:
            # A split failure must not kill the run — degrade to CV-only eval.
            self._log.warning("holdout_split_failed", run_id=run_id,
                              error=str(result.get("error", ""))[:200])
            await self._mark_step(run_id, "completed")
            return {"holdout_path": "", "holdout_frac": 0.0}

        data = self.result_dict(result)
        # Boundary: a malformed RESULT (split=True but paths missing) must not
        # KeyError — degrade to CV-only evaluation on the full dataset.
        if data.get("split") and not (data.get("train_path") and data.get("holdout_path")):
            self._log.warning("split_result_malformed", run_id=run_id)
            data = {"split": False, "n_total": data.get("n_total", "?")}
        data.setdefault("n_train", "?")
        data.setdefault("n_holdout", "?")
        data.setdefault("stratified", False)
        if not data.get("split"):
            await self._log_decision(
                run_id=run_id,
                decision="No holdout reserved — dataset too small; evaluation falls back to cross-validation",
                reasoning=f"{data.get('n_total', '?')} rows < {_MIN_ROWS_FOR_HOLDOUT} minimum. "
                          "A 20% holdout would be too small to estimate generalization reliably.",
                result_summary="evaluation_basis=cv",
            )
            await self.emit(run_id, "Dataset too small for a holdout — using cross-validation for the final score.")
            await self._mark_step(run_id, "completed")
            return {"holdout_path": "", "holdout_frac": 0.0}

        cap_note = ""
        if data.get("train_capped"):
            lc = data.get("learning_curve") or {}
            lc_txt = ", ".join(f"{k} rows→{v}" for k, v in lc.items() if k != "error")
            if data.get("cap_escalated"):
                cap_note = (
                    f"; learning curve still climbing ({lc_txt}) — cap ESCALATED "
                    f"{max_train_rows} → {data['n_train']} rows (MAX_TRAIN_ROWS_HARD={max_hard_rows}) "
                    f"so rare patterns are not lost"
                )
            else:
                cap_note = (
                    f"; training set capped {data.get('n_train_full')} → {data['n_train']} rows "
                    f"(MAX_TRAIN_ROWS={max_train_rows})"
                    + (f"; learning-curve proof the cap is safe: {lc_txt} (plateaued)" if lc_txt else "")
                )
        entry = await self._log_decision(
            run_id=run_id,
            decision=(
                f"Reserved a {int(holdout_frac*100)}% holdout: "
                f"{data['n_train']} train / {data['n_holdout']} holdout "
                f"({'stratified' if data['stratified'] else 'random'})" + cap_note
            ),
            reasoning=(
                "The holdout is split from the RAW data before preprocessing, feature "
                "selection, model selection, and tuning — so the final score reported on it "
                "is an unbiased generalization estimate, not the selection CV."
            ),
            code_executed=code[:300],
            result_summary=f"train={data['n_train']}, holdout={data['n_holdout']}",
        )
        await self.emit(
            run_id,
            f"Holdout reserved: {data['n_train']} train / {data['n_holdout']} held out.",
            {"n_train": data["n_train"], "n_holdout": data["n_holdout"]},
        )
        await self._mark_step(run_id, "completed")

        # Repoint dataset_path at the train split — every downstream agent now
        # reads train-only data automatically.
        return {
            "dataset_path": data["train_path"],
            "train_full_path": data.get("train_full_path", ""),
            "holdout_path": data["holdout_path"],
            "holdout_frac": holdout_frac,
            "decision_log": state.get("decision_log", []) + [entry],
        }
