"""
Phase 3 — Inference & Drift Monitoring

  POST   /runs/{run_id}/deploy       — promote a completed run to a live endpoint
  DELETE /runs/{run_id}/deploy       — stop serving
  GET    /deployments                — list all deployments
  GET    /runs/{run_id}/schema       — feature columns + example values (drives the UI form)
  POST   /runs/{run_id}/predict      — serve predictions (logged for drift)
  GET    /runs/{run_id}/predictions  — recent prediction log
  GET    /runs/{run_id}/drift        — PSI + KS drift report (training data vs live traffic)

Design notes:
- Predictions execute inside the SANDBOX container (the only container with the
  full ML stack), following the project's standing pattern: backend orchestrates,
  sandbox computes. Latency ~1s/request — acceptable for v1.
- Drift is computed with PSI + Kolmogorov–Smirnov from first principles (numpy/
  scipy already in the sandbox) instead of adding the heavy Evidently dependency.
"""

import csv
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.config import settings
from app.core import metrics
from app.core.logging import get_logger
from app.database import AsyncSessionLocal
from app.models.run import Deployment, PredictionLog, Run
from app.sandbox.executor import get_executor

router = APIRouter(prefix="/api/v1", tags=["inference"])
_log = get_logger("inference")


class PredictRequest(BaseModel):
    rows: list[dict[str, Any]]


# ── Sandbox code templates ─────────────────────────────────────────────────────
# Values injected via repr() — same convention as the exporter agent.

PREDICT_CODE = '''
import joblib
import pandas as pd
import numpy as np
import os

pipeline_path = os.path.join(artifacts_dir, "inference_pipeline.pkl")
data = joblib.load(pipeline_path)
preprocessor = data["preprocessor"]
model = data["model"]
threshold = data.get("threshold", 0.5)
target_classes = data.get("target_classes") or []
task_type = data.get("task_type") or "unknown"
multilabel_binarizer = data.get("multilabel_binarizer")

rows = INPUT_ROWS
X_raw = pd.DataFrame(rows)   # original input — engineered formulas reference raw column names
X = X_raw.copy()

# Align incoming columns to the schema the preprocessor was fit on:
# real traffic has extra columns (IDs) and may miss some — drop extras,
# add missing as NaN (the pipeline's imputers handle NaN downstream).
if preprocessor is not None and hasattr(preprocessor, "feature_names_in_"):
    expected = list(preprocessor.feature_names_in_)
    for col in expected:
        if col not in X.columns:
            X[col] = np.nan
    X = X[expected]

# TF-IDF text columns reject NaN — fill object-typed gaps with empty string
for col in X.columns:
    if X[col].dtype == object:
        X[col] = X[col].fillna("")

if preprocessor is not None:
    X_t = preprocessor.transform(X)
    try:
        names = list(preprocessor.get_feature_names_out())
    except Exception:
        names = [f"f_{i}" for i in range(X_t.shape[1])]
    X_t = pd.DataFrame(X_t, columns=names)
else:
    X_t = X.reset_index(drop=True)

# Reproduce LLM-engineered features — the model was trained on
# preprocessed + engineered columns, in this exact order.
engineered = data.get("engineered_features") or []
for feat in engineered:
    fill = feat.get("fill_value", 0.0)
    try:
        ctx = {col: X_raw[col] for col in X_raw.columns}
        ctx.update({"pd": pd, "np": np, "df": X_raw})
        col = eval(feat["formula"], {"__builtins__": {}}, ctx)
        col = pd.Series(col).reset_index(drop=True)
        col = pd.to_numeric(col, errors="coerce")
        col = col.replace([np.inf, -np.inf], np.nan).fillna(fill)
    except Exception:
        col = pd.Series([fill] * len(X_t))
    X_t[feat["name"]] = col.values



def decode(label):
    """Map integer class back to original label when target was encoded."""
    try:
        i = int(label)
        if target_classes and 0 <= i < len(target_classes):
            return target_classes[i]
    except (ValueError, TypeError):
        pass
    return label

predictions = []
if task_type == "multilabel_classification":
    # MultiOutputClassifier → binary matrix (n_samples, n_labels). Decode each row
    # to a SET of label names via the saved MultiLabelBinarizer.
    y_bin = np.asarray(model.predict(X_t))
    if multilabel_binarizer is not None:
        label_sets = multilabel_binarizer.inverse_transform(y_bin)
        for labels in label_sets:
            predictions.append({"prediction": [str(l) for l in labels], "confidence": None})
    else:
        for row in y_bin:
            on = [str(i) for i, v in enumerate(row) if int(v) == 1]
            predictions.append({"prediction": on, "confidence": None})
elif hasattr(model, "predict_proba"):
    proba = model.predict_proba(X_t)
    if proba.shape[1] == 2:
        for p in proba:
            conf = float(p[1])
            label = int(conf >= threshold)
            predictions.append({"prediction": str(decode(label)), "confidence": conf})
    else:
        classes = getattr(model, "classes_", list(range(proba.shape[1])))
        for p in proba:
            i = int(np.argmax(p))
            predictions.append({"prediction": str(decode(classes[i])), "confidence": float(p[i])})
else:
    for v in model.predict(X_t):
        predictions.append({"prediction": str(round(float(v), 6)), "confidence": None})

RESULT = {"predictions": predictions}
'''

DRIFT_CODE = '''
import pandas as pd
import numpy as np
import json
from scipy import stats

# Reference distribution: the data the model was trained on
reference = pd.read_csv(dataset_path)
target_col = TARGET_COL
exclude = EXCLUDE_COLS
current = pd.DataFrame(CURRENT_ROWS)

drop = [c for c in [target_col] + exclude if c in reference.columns]
reference = reference.drop(columns=drop, errors="ignore")

# Only compare features present in both
common = [c for c in reference.columns if c in current.columns]


def psi(ref, cur, bins=10):
    """Population Stability Index. <0.1 stable, 0.1-0.25 moderate, >0.25 major drift."""
    ref = pd.Series(ref).dropna()
    cur = pd.Series(cur).dropna()
    if len(ref) == 0 or len(cur) == 0:
        return None
    if pd.api.types.is_numeric_dtype(ref) and ref.nunique() > 10:
        edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
        if len(edges) < 3:
            return 0.0
        edges[0], edges[-1] = -np.inf, np.inf
        ref_pct = np.histogram(ref, bins=edges)[0] / len(ref)
        cur_pct = np.histogram(pd.to_numeric(cur, errors="coerce").dropna(), bins=edges)[0] / max(len(cur), 1)
    else:
        cats = ref.astype(str).value_counts(normalize=True)
        cur_counts = cur.astype(str).value_counts(normalize=True)
        all_cats = sorted(set(cats.index) | set(cur_counts.index))
        ref_pct = np.array([cats.get(c, 0) for c in all_cats])
        cur_pct = np.array([cur_counts.get(c, 0) for c in all_cats])
    ref_pct = np.clip(ref_pct, 1e-6, None)
    cur_pct = np.clip(cur_pct, 1e-6, None)
    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


features = []
for col in common:
    entry = {"feature": col, "psi": None, "ks_pvalue": None}
    try:
        entry["psi"] = round(psi(reference[col], current[col]), 4)
        if pd.api.types.is_numeric_dtype(reference[col]):
            ref_clean = reference[col].dropna()
            cur_clean = pd.to_numeric(current[col], errors="coerce").dropna()
            if len(cur_clean) >= 5:
                ks = stats.ks_2samp(ref_clean, cur_clean)
                entry["ks_pvalue"] = round(float(ks.pvalue), 4)
    except Exception as e:
        entry["error"] = str(e)[:100]
    p = entry.get("psi")
    entry["status"] = (
        "stable" if p is not None and p < 0.1
        else "moderate" if p is not None and p < 0.25
        else "drifted" if p is not None
        else "unknown"
    )
    features.append(entry)

psi_values = [f["psi"] for f in features if f["psi"] is not None]
max_psi = max(psi_values) if psi_values else 0.0
n_drifted = sum(1 for f in features if f["status"] == "drifted")

RESULT = {
    "features": features,
    "max_psi": round(max_psi, 4),
    "n_features_checked": len(features),
    "n_drifted": n_drifted,
    "n_samples_current": len(current),
    "n_samples_reference": len(reference),
    "overall_status": "drifted" if n_drifted > 0 else ("moderate" if max_psi >= 0.1 else "stable"),
}
'''


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_run_or_404(db, run_id: str) -> Run:
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


async def _get_active_deployment(db, run_id: str) -> Deployment | None:
    result = await db.execute(
        select(Deployment).where(
            Deployment.run_id == run_id, Deployment.status == "active"
        )
    )
    return result.scalar_one_or_none()


# ── Deploy / stop ─────────────────────────────────────────────────────────────

@router.post("/runs/{run_id}/deploy")
async def deploy_model(run_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        run = await _get_run_or_404(db, run_id)
        if run.status != "completed":
            raise HTTPException(status_code=400, detail=f"Run is {run.status} — only completed runs can be deployed")

        pipeline_file = Path(settings.DATA_DIR) / run_id / "artifacts" / "inference_pipeline.pkl"
        if not pipeline_file.exists():
            raise HTTPException(status_code=400, detail="inference_pipeline.pkl not found in artifacts")

        existing = await _get_active_deployment(db, run_id)
        if existing:
            return {"deployment_id": existing.id, "status": "active", "already_deployed": True}

        # Reactivate a stopped deployment if one exists (run_id is unique)
        prev = await db.execute(select(Deployment).where(Deployment.run_id == run_id))
        deployment = prev.scalar_one_or_none()
        if deployment:
            deployment.status = "active"
            deployment.stopped_at = None
        else:
            deployment = Deployment(run_id=run_id, status="active")
            db.add(deployment)
        await db.commit()
        await db.refresh(deployment)

    metrics.active_deployments.inc()
    _log.info("model_deployed", run_id=run_id, deployment_id=deployment.id)
    return {
        "deployment_id": deployment.id,
        "run_id": run_id,
        "status": "active",
        "predict_url": f"/api/v1/runs/{run_id}/predict",
        "model": run.winner_model,
    }


@router.delete("/runs/{run_id}/deploy")
async def stop_deployment(run_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        await _get_run_or_404(db, run_id)
        deployment = await _get_active_deployment(db, run_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="No active deployment for this run")
        deployment.status = "stopped"
        deployment.stopped_at = datetime.now(timezone.utc)
        await db.commit()

    metrics.active_deployments.dec()
    _log.info("model_undeployed", run_id=run_id)
    return {"run_id": run_id, "status": "stopped"}


@router.get("/deployments")
async def list_deployments() -> dict:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Deployment, Run)
            .join(Run, Deployment.run_id == Run.id)
            .order_by(Deployment.deployed_at.desc())
        )
        rows = result.all()
        deployments = [
            {
                "deployment_id": d.id,
                "run_id": d.run_id,
                "status": d.status,
                "model": r.winner_model,
                "task_type": r.task_type,
                "metric": r.primary_metric,
                "score": r.final_score,
                "n_predictions": d.n_predictions,
                "deployed_at": d.deployed_at.isoformat() if d.deployed_at else None,
            }
            for d, r in rows
        ]
    return {"deployments": deployments}


# ── Feature schema (drives the auto-generated UI form) ────────────────────────

@router.get("/runs/{run_id}/schema")
async def get_feature_schema(run_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        run = await _get_run_or_404(db, run_id)

    dataset = Path(settings.DATA_DIR) / run_id / "dataset.csv"
    if not dataset.exists():
        raise HTTPException(status_code=404, detail="Training dataset not found")

    excluded = set(run.exclude_columns or [])
    if run.target_column:
        excluded.add(run.target_column)

    # stdlib CSV read — header + a few sample rows, no pandas needed in backend
    with open(dataset, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        sample_rows = []
        for i, row in enumerate(reader):
            sample_rows.append(row)
            if i >= 4:
                break

    if not sample_rows:
        raise HTTPException(status_code=400, detail="Dataset is empty")

    def infer_type(values: list[str]) -> str:
        for v in values:
            if v in ("", None):
                continue
            try:
                float(v)
            except ValueError:
                return "text"
        return "number"

    features = []
    for col in sample_rows[0].keys():
        if col in excluded:
            continue
        col_values = [r.get(col, "") for r in sample_rows]
        features.append({
            "name": col,
            "type": infer_type(col_values),
            "example": next((v for v in col_values if v not in ("", None)), ""),
        })

    return {
        "run_id": run_id,
        "target_column": run.target_column,
        "task_type": run.task_type,
        "features": features,
    }


# ── Predict ───────────────────────────────────────────────────────────────────

@router.post("/runs/{run_id}/predict")
async def predict(run_id: str, request: PredictRequest) -> dict:
    if not request.rows:
        raise HTTPException(status_code=400, detail="rows must not be empty")
    if len(request.rows) > 100:
        raise HTTPException(status_code=400, detail="Max 100 rows per request")

    async with AsyncSessionLocal() as db:
        run = await _get_run_or_404(db, run_id)
        deployment = await _get_active_deployment(db, run_id)
        if not deployment:
            raise HTTPException(status_code=409, detail="Model not deployed — POST /deploy first")

    t0 = time.perf_counter()
    executor = get_executor()

    # Persistent-serving fast path: the sandbox keeps loaded pipelines in an
    # in-process cache (trusted fixed code, no exec) → ms latency instead of a
    # process spawn + pickle reload per call. Falls back to the exec path.
    result = None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{executor.base_url}/predict",
                                     json={"run_id": run_id, "rows": request.rows})
        if resp.status_code == 200:
            result = {"success": True, "result": resp.json()}
    except Exception as exc:
        _log.warning("fast_predict_unavailable_fallback_exec", run_id=run_id, error=str(exc)[:120])

    if result is None:
        code = PREDICT_CODE.replace("INPUT_ROWS", repr(request.rows))
        result = await executor.execute(code, run_id, timeout=60)
    latency_ms = (time.perf_counter() - t0) * 1000

    if not result.get("success"):
        metrics.predictions_total.labels(run_id=run_id, status="error").inc()
        _log.error("prediction_failed", run_id=run_id, error=str(result.get("error", ""))[:200])
        raise HTTPException(status_code=500, detail=f"Prediction failed: {result.get('error', '')[:300]}")

    predictions = result["result"]["predictions"]
    per_row_latency = latency_ms / len(predictions)

    # Log every prediction — this is the raw material for drift detection
    async with AsyncSessionLocal() as db:
        for row, pred in zip(request.rows, predictions):
            pv = pred["prediction"]
            # multilabel predictions are lists — store as JSON for clean round-trip
            pred_str = __import__("json").dumps(pv) if isinstance(pv, list) else str(pv)
            db.add(PredictionLog(
                run_id=run_id,
                features=row,
                prediction=pred_str,
                confidence=pred.get("confidence"),
                latency_ms=round(per_row_latency, 1),
            ))
        dep_q = await db.execute(select(Deployment).where(Deployment.run_id == run_id))
        dep = dep_q.scalar_one_or_none()
        if dep:
            dep.n_predictions += len(predictions)
        await db.commit()

    metrics.predictions_total.labels(run_id=run_id, status="success").inc(len(predictions))
    metrics.prediction_latency_seconds.observe(latency_ms / 1000)

    return {
        "run_id": run_id,
        "predictions": predictions,
        "model": run.winner_model,
        "latency_ms": round(latency_ms, 1),
    }


# ── Prediction log ────────────────────────────────────────────────────────────

@router.get("/runs/{run_id}/predictions")
async def list_predictions(run_id: str, limit: int = 50) -> dict:
    async with AsyncSessionLocal() as db:
        await _get_run_or_404(db, run_id)
        result = await db.execute(
            select(PredictionLog)
            .where(PredictionLog.run_id == run_id)
            .order_by(PredictionLog.created_at.desc())
            .limit(min(limit, 200))
        )
        logs = result.scalars().all()

    return {
        "run_id": run_id,
        "predictions": [
            {
                "id": p.id,
                "features": p.features,
                "prediction": p.prediction,
                "confidence": p.confidence,
                "latency_ms": p.latency_ms,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in logs
        ],
    }


# ── Drift report ──────────────────────────────────────────────────────────────

@router.get("/runs/{run_id}/drift")
async def drift_report(run_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        run = await _get_run_or_404(db, run_id)
        result = await db.execute(
            select(PredictionLog)
            .where(PredictionLog.run_id == run_id)
            .order_by(PredictionLog.created_at.desc())
            .limit(1000)
        )
        logs = result.scalars().all()

    if len(logs) < 10:
        return {
            "run_id": run_id,
            "status": "insufficient_data",
            "n_predictions": len(logs),
            "message": f"Need at least 10 logged predictions for drift analysis — have {len(logs)}.",
        }

    current_rows = [p.features for p in logs]
    code = (
        DRIFT_CODE
        .replace("TARGET_COL", repr(run.target_column or ""))
        .replace("EXCLUDE_COLS", repr(run.exclude_columns or []))
        .replace("CURRENT_ROWS", repr(current_rows))
    )

    executor = get_executor()
    result = await executor.execute(code, run_id, timeout=120)
    if not result.get("success"):
        raise HTTPException(status_code=500, detail=f"Drift computation failed: {result.get('error', '')[:300]}")

    report = result["result"]
    metrics.drift_psi_max.set(report.get("max_psi", 0))
    _log.info(
        "drift_report",
        run_id=run_id,
        max_psi=report.get("max_psi"),
        overall=report.get("overall_status"),
    )

    return {"run_id": run_id, "status": "ok", **report}
