"""
Backlog P10–P15 — endpoints that reuse the existing sandbox executor, LLM client,
and DB. Each is self-contained; sandbox code uses token-replace (repr) injection.

  GET  /runs/compare?a=&b=            P10 — run-vs-run diff
  POST /runs/{id}/batch-predict       P13 — score an uploaded CSV
  POST /runs/{id}/explain             P11 — per-prediction SHAP attribution
  POST /runs/{id}/ask                 P14 — grounded Q&A over a run
  GET  /runs/{id}/fairness?columns=   P15 — sliced metrics + disparate impact
  POST /runs/{id}/retrain             P12 — champion/challenger retrain
"""

import csv
import io
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.api.routes.inference import PREDICT_CODE
from app.config import settings
from app.core.llm import get_llm
from app.database import AsyncSessionLocal
from app.models.run import DecisionLog, Deployment, PredictionLog, Run
from app.redis_client import get_run_state
from app.sandbox.executor import get_executor

router = APIRouter(prefix="/api/v1", tags=["extras"])


async def _run_or_404(db, run_id: str) -> Run:
    r = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Run not found")
    return r


# ── P10: run-vs-run comparison ────────────────────────────────────────────────
@router.get("/runs/compare")
async def compare_runs(a: str, b: str) -> dict:
    async with AsyncSessionLocal() as db:
        ra, rb = await _run_or_404(db, a), await _run_or_404(db, b)

        def summary(r: Run) -> dict:
            return {
                "run_id": r.id, "dataset": r.dataset_filename, "task_type": r.task_type,
                "target": r.target_column, "metric": r.primary_metric,
                "baseline": r.baseline_score, "final": r.final_score,
                "winner": r.winner_model, "iterations": r.iteration_count, "status": r.status,
            }

        async def decisions(run_id: str) -> list[dict]:
            rows = (await db.execute(
                select(DecisionLog).where(DecisionLog.run_id == run_id)
                .order_by(DecisionLog.timestamp)
            )).scalars().all()
            return [{"agent": d.agent_name, "decision": d.decision} for d in rows]

        sa, sb = summary(ra), summary(rb)
        delta = None
        if ra.final_score is not None and rb.final_score is not None:
            delta = round(rb.final_score - ra.final_score, 4)
        return {
            "a": sa, "b": sb,
            "final_score_delta": delta,
            "decisions_a": await decisions(a),
            "decisions_b": await decisions(b),
        }


# ── P13: batch scoring ────────────────────────────────────────────────────────
@router.post("/runs/{run_id}/batch-predict")
async def batch_predict(run_id: str, file: UploadFile = File(...)) -> StreamingResponse:
    async with AsyncSessionLocal() as db:
        await _run_or_404(db, run_id)
    raw = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    rows = list(reader)[:5000]
    if not rows:
        raise HTTPException(400, "CSV is empty")

    code = PREDICT_CODE.replace("INPUT_ROWS", repr(rows))
    result = await get_executor().execute(code, run_id, timeout=120)
    if not result.get("success"):
        raise HTTPException(500, f"Batch scoring failed: {str(result.get('error',''))[:300]}")
    preds = result["result"]["predictions"]

    out = io.StringIO()
    fields = list(rows[0].keys()) + ["prediction", "confidence"]
    w = csv.DictWriter(out, fieldnames=fields)
    w.writeheader()
    for row, p in zip(rows, preds):
        pv = p["prediction"]
        row["prediction"] = json.dumps(pv) if isinstance(pv, list) else pv
        row["confidence"] = p.get("confidence")
        w.writerow(row)
    out.seek(0)
    return StreamingResponse(
        iter([out.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="scored_{run_id[:8]}.csv"'},
    )


# ── P11: per-prediction SHAP ──────────────────────────────────────────────────
class ExplainRequest(BaseModel):
    rows: list[dict[str, Any]]


_EXPLAIN_CODE = '''
import joblib, os, numpy as np, pandas as pd
data = joblib.load(os.path.join(artifacts_dir, "inference_pipeline.pkl"))
prep, model = data["preprocessor"], data["model"]
rows = INPUT_ROWS
X = pd.DataFrame(rows)
if prep is not None and hasattr(prep, "feature_names_in_"):
    for c in prep.feature_names_in_:
        if c not in X.columns: X[c] = np.nan
    X = X[list(prep.feature_names_in_)]
    for c in X.columns:
        if X[c].dtype == object: X[c] = X[c].fillna("")
Xt = prep.transform(X) if prep is not None else X.values
try:
    names = list(prep.get_feature_names_out())
except Exception:
    names = [f"f_{i}" for i in range(Xt.shape[1])]
out = []
try:
    import shap
    expl = shap.TreeExplainer(model) if hasattr(model, "feature_importances_") else shap.Explainer(model, Xt)
    sv = expl.shap_values(Xt) if hasattr(expl, "shap_values") else expl(Xt).values
    if isinstance(sv, list): sv = sv[1] if len(sv) > 1 else sv[0]
    sv = np.asarray(sv)
    for i in range(sv.shape[0]):
        contrib = sorted(zip(names, sv[i].tolist()), key=lambda t: -abs(t[1]))[:8]
        out.append([{"feature": n, "contribution": round(float(v), 5)} for n, v in contrib])
except Exception as e:
    out = [{"error": str(e)[:120]}]
RESULT = {"explanations": out}
'''


@router.post("/runs/{run_id}/explain")
async def explain(run_id: str, req: ExplainRequest) -> dict:
    if not req.rows:
        raise HTTPException(400, "rows required")
    code = _EXPLAIN_CODE.replace("INPUT_ROWS", repr(req.rows[:20]))
    result = await get_executor().execute(code, run_id, timeout=90)
    if not result.get("success"):
        raise HTTPException(500, f"Explain failed: {str(result.get('error',''))[:300]}")
    return {"run_id": run_id, **result["result"]}


# ── P14: ask-your-model (grounded Q&A) ────────────────────────────────────────
class AskRequest(BaseModel):
    question: str


@router.post("/runs/{run_id}/ask")
async def ask(run_id: str, req: AskRequest) -> dict:
    async with AsyncSessionLocal() as db:
        run = await _run_or_404(db, run_id)
        logs = (await db.execute(
            select(DecisionLog).where(DecisionLog.run_id == run_id).order_by(DecisionLog.timestamp)
        )).scalars().all()
    state = await get_run_state(run_id)
    context = {
        "task_type": run.task_type, "target": run.target_column, "metric": run.primary_metric,
        "baseline": run.baseline_score, "final": run.final_score, "winner": run.winner_model,
        "shap_top_features": (state or {}).get("shap_top_features"),
        "decisions": [{"agent": d.agent_name, "decision": d.decision, "why": d.reasoning} for d in logs],
    }
    system = (
        "You answer questions about a completed AutoML run STRICTLY from the provided JSON "
        "context (decisions, metrics, SHAP). Never invent numbers. If the context lacks the "
        'answer, say so. Respond as JSON: {"answer": "...", "cited_agents": ["..."]}'
    )
    user = f"Run context:\n{json.dumps(context, default=str)[:6000]}\n\nQuestion: {req.question}"
    try:
        resp = await get_llm().complete_json(system, user)
    except Exception as e:
        raise HTTPException(500, f"Ask failed: {str(e)[:200]}")
    return {"run_id": run_id, **resp}


# ── P15: fairness audit ───────────────────────────────────────────────────────
_FAIRNESS_CODE = '''
import joblib, os, numpy as np, pandas as pd
from sklearn.metrics import accuracy_score, recall_score
data = joblib.load(os.path.join(artifacts_dir, "inference_pipeline.pkl"))
prep, model = data["preprocessor"], data["model"]
df = pd.read_csv(dataset_path)
target_col = TARGET_COL
sensitive = SENSITIVE_COLS
groups = {}
for col in sensitive:
    if col not in df.columns: continue
    col_out = {}
    for val in df[col].dropna().astype(str).value_counts().head(8).index:
        sub = df[df[col].astype(str) == val]
        if len(sub) < 20: continue
        X = sub.drop(columns=[c for c in [target_col] + sensitive if c in sub.columns], errors="ignore")
        try:
            if prep is not None and hasattr(prep, "feature_names_in_"):
                for c in prep.feature_names_in_:
                    if c not in X.columns: X[c] = np.nan
                X = X[list(prep.feature_names_in_)]
                for c in X.columns:
                    if X[c].dtype == object: X[c] = X[c].fillna("")
            Xt = prep.transform(X) if prep is not None else X.values
            pred = model.predict(Xt)
            y = sub[target_col]
            if not pd.api.types.is_numeric_dtype(y):
                y = y.astype("category").cat.codes
            col_out[str(val)] = {
                "n": int(len(sub)),
                "selection_rate": round(float(np.mean(np.asarray(pred).astype(float) > 0)), 4),
                "accuracy": round(float(accuracy_score(y, pred)), 4),
            }
        except Exception as e:
            col_out[str(val)] = {"error": str(e)[:80]}
    # disparate impact: min/max selection rate ratio (80% rule)
    rates = [v["selection_rate"] for v in col_out.values() if "selection_rate" in v]
    di = round(min(rates) / max(rates), 3) if rates and max(rates) > 0 else None
    groups[col] = {"by_group": col_out, "disparate_impact_ratio": di,
                   "passes_80pct_rule": (di is not None and di >= 0.8)}
RESULT = {"fairness": groups}
'''


@router.get("/runs/{run_id}/fairness")
async def fairness(run_id: str, columns: str) -> dict:
    cols = [c.strip() for c in columns.split(",") if c.strip()]
    if not cols:
        raise HTTPException(400, "columns query param required (comma-separated)")
    async with AsyncSessionLocal() as db:
        run = await _run_or_404(db, run_id)
    code = (_FAIRNESS_CODE
            .replace("TARGET_COL", repr(run.target_column or ""))
            .replace("SENSITIVE_COLS", repr(cols)))
    result = await get_executor().execute(code, run_id, timeout=120)
    if not result.get("success"):
        raise HTTPException(500, f"Fairness audit failed: {str(result.get('error',''))[:300]}")
    return {"run_id": run_id, **result["result"]}


# ── DL-3: autoencoder anomaly scoring over a run's processed data ──────────────
_ANOMALY_CODE = '''
import pandas as pd
import numpy as np
import os
import json
import warnings
warnings.filterwarnings("ignore")
from automl_dl import TorchAutoencoder

run_dir = os.path.dirname(artifacts_dir.rstrip("/"))
proc = os.path.join(artifacts_dir, "processed.csv")
if not os.path.exists(proc):
    RESULT = {"error": "processed.csv not found — run the pipeline first"}
else:
    df = pd.read_csv(proc)
    X = df.drop(columns=[c for c in ["__target__"] if c in df.columns])
    X = X.select_dtypes(include=[np.number]).fillna(0.0)
    contamination = CONTAMINATION
    n = min(len(X), 50000)   # cap for the interactive tool
    Xs = X.iloc[:n].values.astype("float32")
    ae = TorchAutoencoder(contamination=contamination, max_epochs=40, random_state=42).fit(Xs)
    err = -ae.score_samples(Xs)
    flags = ae.predict(Xs)
    order = np.argsort(-err)[:20]
    RESULT = {
        "n_scored": int(n),
        "contamination": contamination,
        "threshold": float(ae.threshold_),
        "n_flagged": int((flags == -1).sum()),
        "pct_flagged": round(float((flags == -1).mean()) * 100, 2),
        "error_percentiles": {p: round(float(np.percentile(err, p)), 5) for p in (50, 90, 99)},
        "top_anomalies": [{"row": int(i), "reconstruction_error": round(float(err[i]), 5)} for i in order],
    }
'''


@router.post("/runs/{run_id}/anomaly")
async def anomaly_scan(run_id: str, contamination: float = 0.05) -> dict:
    """DL-3 — score a completed run's processed data for anomalies with a PyTorch
    autoencoder (reconstruction error). Contained tool over the existing artifacts;
    needs the sandbox image built with torch (DL_ENABLED)."""
    if not (0.001 <= contamination <= 0.5):
        raise HTTPException(400, "contamination must be between 0.001 and 0.5")
    async with AsyncSessionLocal() as db:
        await _run_or_404(db, run_id)
    code = _ANOMALY_CODE.replace("CONTAMINATION", repr(float(contamination)))
    result = await get_executor().execute(code, run_id, timeout=300)
    if not result.get("success"):
        raise HTTPException(500, f"Anomaly scan failed: {str(result.get('error',''))[:300]}")
    data = result["result"] or {}
    if data.get("error"):
        raise HTTPException(409, data["error"])
    return {"run_id": run_id, **data}


# ── Decision analysis: threshold sweep + campaign simulator + calibration ─────
# Turns "probability = 0.73" into "who should we contact, and what's the payoff".
# Computed from the REAL model + the honest holdout (the same holdout the trust gate
# scored), mirroring the evaluator's holdout reconstruction. Binary classification.
_DECISION_CODE = '''
import pandas as pd
import numpy as np
import json
import joblib
import warnings
warnings.filterwarnings("ignore")
from sklearn.metrics import precision_score, recall_score, f1_score, brier_score_loss
from sklearn.calibration import calibration_curve

model_path = MODEL_PATH
preprocessor_path = PREPROCESSOR_PATH
holdout_path = HOLDOUT_PATH
enriched_path = ENRICHED_PATH
target_col = TARGET_COL
target_classes = TARGET_CLASSES
engineered = ENGINEERED

model = joblib.load(model_path)
if not hasattr(model, "predict_proba"):
    RESULT = {"error": "model has no probability output (predict_proba)"}
else:
    raw = pd.read_csv(holdout_path)
    raw = raw[raw[target_col].notna()].reset_index(drop=True)
    traw = raw[target_col]
    if target_classes:
        idx = {c: i for i, c in enumerate(target_classes)}
        y = traw.astype(str).map(idx)
    else:
        y = pd.to_numeric(traw, errors="coerce")
    keep = y.notna()
    raw, y = raw[keep].reset_index(drop=True), y[keep].astype(int).values
    n_classes = len(set(y.tolist()))
    if n_classes != 2:
        RESULT = {"error": f"decision analysis is for BINARY classification (found {n_classes} classes)"}
    else:
        # Rebuild features exactly like the evaluator/inference path.
        X_raw = raw.copy()
        Xh = raw.copy()
        pre = joblib.load(preprocessor_path) if preprocessor_path else None
        if pre is not None and hasattr(pre, "feature_names_in_"):
            for c in pre.feature_names_in_:
                if c not in Xh.columns:
                    Xh[c] = np.nan
            Xh = Xh[list(pre.feature_names_in_)]
        for c in Xh.columns:
            if Xh[c].dtype == object:
                Xh[c] = Xh[c].fillna("")
        Xt = pre.transform(Xh) if pre is not None else Xh.values
        try:
            names = list(pre.get_feature_names_out())
        except Exception:
            names = ["f_" + str(i) for i in range(Xt.shape[1])]
        Xt = pd.DataFrame(Xt, columns=names)
        for feat in engineered:
            fill = feat.get("fill_value", 0.0)
            try:
                ctx = {c: X_raw[c] for c in X_raw.columns}
                ctx.update({"pd": pd, "np": np, "df": X_raw})
                col = eval(feat["formula"], {"__builtins__": {}}, ctx)
                col = pd.to_numeric(pd.Series(col).reset_index(drop=True), errors="coerce")
                col = col.replace([np.inf, -np.inf], np.nan).fillna(fill)
            except Exception:
                col = pd.Series([fill] * len(Xt))
            Xt[feat["name"]] = col.values
        # Align to the training column order (enriched.csv minus __target__).
        try:
            train_cols = [c for c in pd.read_csv(enriched_path, nrows=0).columns if c != "__target__"]
            Xt = Xt.reindex(columns=train_cols, fill_value=0)
        except Exception:
            pass

        proba = model.predict_proba(Xt)[:, 1]
        n = len(y)
        base_rate = float(y.mean())            # holdout positive rate
        total_pos = int(y.sum())

        # 1) THRESHOLD SWEEP — precision/recall/contacts at each operating point.
        sweep = []
        for t in np.round(np.arange(0.1, 0.91, 0.1), 2):
            pred = (proba >= t).astype(int)
            contacted = int(pred.sum())
            sweep.append({
                "threshold": float(t),
                "precision": round(float(precision_score(y, pred, zero_division=0)), 4),
                "recall": round(float(recall_score(y, pred, zero_division=0)), 4),
                "f1": round(float(f1_score(y, pred, zero_division=0)), 4),
                "contacts": contacted,
                "contacts_pct": round(100.0 * contacted / n, 1),
            })

        # 2) CAMPAIGN SIMULATOR — rank by score, contact top-K%, vs contact-everyone.
        order = np.argsort(-proba)
        y_sorted = y[order]
        campaign = []
        for k in (10, 20, 30, 50):
            m = max(1, int(n * k / 100))
            captured = int(y_sorted[:m].sum())
            campaign.append({
                "contact_pct": k,
                "customers_contacted": m,
                "conversions_captured": captured,
                "conversions_retained_pct": round(100.0 * captured / total_pos, 1) if total_pos else 0.0,
                "contacts_avoided": n - m,
                "lift_vs_random": round((captured / m) / base_rate, 2) if base_rate > 0 else None,
            })
        contact_all = {"customers_contacted": n, "conversions": total_pos,
                       "positive_rate_pct": round(100.0 * base_rate, 1)}

        # 3) CALIBRATION — reliability curve + Brier score (lower = better calibrated).
        brier = float(brier_score_loss(y, proba))
        try:
            frac_pos, mean_pred = calibration_curve(y, proba, n_bins=10, strategy="quantile")
            reliability = [{"predicted": round(float(mp), 4), "actual": round(float(fp), 4)}
                           for mp, fp in zip(mean_pred, frac_pos)]
            ece = float(np.mean(np.abs(np.array([r["predicted"] for r in reliability]) -
                                       np.array([r["actual"] for r in reliability]))))
        except Exception:
            reliability, ece = [], None

        # Hand the raw scores back so the BACKEND can run the tested
        # rare_event / business_objective modules on them, rather than this
        # template carrying a second copy of that maths (the drift bug family).
        # Capped: a multi-million-row holdout must not be JSON-round-tripped.
        _CAP = 200000
        if n > _CAP:
            _rs = np.random.RandomState(42)
            _sel = _rs.choice(n, _CAP, replace=False)
            _y_out, _p_out, _truncated = y[_sel], proba[_sel], True
        else:
            _y_out, _p_out, _truncated = y, proba, False

        RESULT = {
            "n_holdout": n,
            "positive_rate": round(base_rate, 4),
            "_scores": {"y": [int(v) for v in _y_out],
                        "p": [float(v) for v in _p_out],
                        "sampled": bool(_truncated), "cap": _CAP},
            "threshold_sweep": sweep,
            "campaign": {"top_k": campaign, "contact_all": contact_all},
            "calibration": {"brier_score": round(brier, 4),
                            "expected_calibration_error": round(ece, 4) if ece is not None else None,
                            "well_calibrated": bool(ece is not None and ece < 0.05),
                            "reliability_curve": reliability},
        }
'''


@router.get("/runs/{run_id}/decision-analysis")
async def decision_analysis(run_id: str) -> dict:
    """Threshold analysis + campaign/business-impact simulator + calibration
    (Brier + reliability) computed from the REAL model on the honest holdout.
    Binary classification only. Answers 'who should we act on, and what's the
    payoff' — not just 'what's the AUC'."""
    async with AsyncSessionLocal() as db:
        await _run_or_404(db, run_id)          # 404 for a genuinely unknown run
    from app.redis_client import get_run_state
    try:
        state = await get_run_state(run_id) or {}
    except Exception:
        state = {}                              # a Redis blip is a 409, never a 500
    model_path = state.get("tuned_model_path") or state.get("winner_model_path")
    holdout_path = state.get("holdout_path")
    if not model_path or not holdout_path:
        raise HTTPException(409, "Run has no model/holdout yet — complete a run first.")
    engineered = [{"name": f.get("name"), "formula": f.get("formula"),
                   "fill_value": f.get("fill_value", 0.0)}
                  for f in (state.get("features_created") or []) if isinstance(f, dict) and f.get("formula")]
    code = (_DECISION_CODE
            .replace("MODEL_PATH", repr(model_path))
            .replace("PREPROCESSOR_PATH", repr(state.get("preprocessor_path", "")))
            .replace("HOLDOUT_PATH", repr(holdout_path))
            .replace("ENRICHED_PATH", repr(state.get("enriched_data_path", "")))
            .replace("TARGET_COL", repr(state.get("target_column", "")))
            .replace("TARGET_CLASSES", repr(state.get("target_classes") or []))
            .replace("ENGINEERED", repr(engineered)))
    result = await get_executor().execute(code, run_id, timeout=180)
    if not result.get("success"):
        raise HTTPException(500, f"Decision analysis failed: {str(result.get('error',''))[:300]}")
    data = result["result"] or {}
    if data.get("error"):
        raise HTTPException(409, data["error"])

    # ── Phase 3.5: budget curve + business-objective operating point ─────────
    # Computed HERE, from app.core.rare_event / app.core.business_objective, so
    # the numbers come from the unit-tested implementations rather than a copy
    # embedded in the sandbox template.
    scores = data.pop("_scores", None) or {}
    y_s, p_s = scores.get("y") or [], scores.get("p") or []
    if y_s and p_s and len(y_s) == len(p_s):
        from app.core import rare_event
        from app.core.business_objective import BusinessObjective, optimize_threshold

        data["top_k"] = rare_event.top_k_table(y_s, p_s)
        data["top_k"]["sampled"] = bool(scores.get("sampled"))
        if scores.get("sampled"):
            data["top_k"]["note"] = (
                f"computed on a random sample of {scores.get('cap')} holdout rows "
                f"(of {data.get('n_holdout')}) — ranking metrics are stable at this size")

        obj_raw = state.get("business_objective") or {}
        if obj_raw:
            obj = BusinessObjective.from_dict(obj_raw)
            data["operating_point"] = optimize_threshold(y_s, p_s, obj)
        else:
            data["operating_point"] = {
                "error": "no business objective was set for this run — a threshold cannot "
                         "be chosen without knowing what a true positive is worth and what "
                         "a false positive costs. The threshold_sweep above shows the "
                         "trade-off; picking a point on it is a business decision."}

    return {"run_id": run_id, **data}


# ── P12: champion/challenger retrain ──────────────────────────────────────────
@router.post("/runs/{run_id}/retrain")
async def retrain(run_id: str, background_tasks: BackgroundTasks) -> dict:
    async with AsyncSessionLocal() as db:
        champ = await _run_or_404(db, run_id)
        new_id = str(uuid.uuid4())
        challenger = Run(
            id=new_id, status="queued",
            dataset_filename=champ.dataset_filename, dataset_path=champ.dataset_path,
            user_goal=champ.user_goal, exclude_columns=champ.exclude_columns or [],
            fp_fn_preference=champ.fp_fn_preference, interpretability_required=champ.interpretability_required,
            pipeline=getattr(champ, "pipeline", "tabular"),
        )
        db.add(challenger)
        await db.flush()

    from app.agents.orchestrator import run_pipeline
    background_tasks.add_task(run_pipeline, new_id)
    return {
        "champion_run_id": run_id, "challenger_run_id": new_id,
        "note": "Challenger training started. Compare via GET /runs/compare?a={champ}&b={challenger}; "
                "promote only if the challenger's final score beats the champion.",
    }
