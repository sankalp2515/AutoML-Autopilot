"""Bring your own incident: two CSV windows plus a short description of the model.

A user uploads
  * a **reference** window: data from when the model was trained and healthy, with labels;
  * a **current** window: recent production inputs, with labels where they have arrived.

and then says what the target is, which label is the positive class, which metric matters,
which model family is in production (logistic regression or gradient boosting), the
serving threshold, and optionally a segment that must not get worse, what changed recently,
an error log, package versions before/after, and public documentation URLs.

Autopilot rebuilds the production model *family* from the reference window (it does not
load the user's model binary: unpickling an uploaded model would let anyone run code on
the server) and then investigates exactly as it does for the catalog incidents.

Limits are enforced before anything is parsed into memory. Raw rows never leave the
server: Nemotron sees aggregates and column names, Tavily sees library identifiers only.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import settings

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 200_000
MAX_COLS = 200
MIN_REFERENCE_ROWS = 300
MAX_CATEGORY_LEVELS = 50
_ID = re.compile(r"^[0-9a-f]{12}$")
_PKG = re.compile(r"^[A-Za-z0-9_.\-]{1,40}$")
_VER = re.compile(r"^\d+(\.\d+){0,3}[A-Za-z0-9.+\-]*$")
WATCHED_PACKAGES = ("scikit-learn", "pandas", "numpy", "scipy", "xgboost", "lightgbm", "joblib")


class UploadError(ValueError):
    """A problem with the user's files or settings; the message is shown to them."""


class UploadSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=3, max_length=120)
    symptom: str = Field(min_length=3, max_length=600, description="What you observed")
    notes: str = Field(default="", max_length=3000, description="What changed recently, team context")
    target: str = Field(min_length=1, max_length=120)
    positive_label: str = Field(min_length=1, max_length=120)
    metric: Literal["f1", "recall", "precision", "balanced_accuracy"] = "f1"
    model_family: Literal["logistic_regression", "hist_gradient_boosting"] = "logistic_regression"
    serving_threshold: float = Field(default=0.5, gt=0, lt=1)
    trained_threshold: float | None = Field(default=None, gt=0, lt=1)
    segment_column: str | None = Field(default=None, max_length=120)
    segment_value: str | None = Field(default=None, max_length=120)
    segment_max_drop: float = Field(default=0.05, ge=0, le=0.5)
    exclude_columns: list[str] = Field(default_factory=list, max_length=MAX_COLS)
    packages_before: dict[str, str] = Field(default_factory=dict)
    packages_after: dict[str, str] = Field(default_factory=dict)
    error_log: str = Field(default="", max_length=6000)
    docs_urls: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("packages_before", "packages_after")
    @classmethod
    def _pkgs(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 30:
            raise ValueError("at most 30 packages")
        for k, ver in v.items():
            if not _PKG.match(k) or not _VER.match(str(ver)):
                raise ValueError(f"invalid package/version: {k} {ver}")
        return {k.lower(): str(ver) for k, ver in v.items()}

    @field_validator("docs_urls")
    @classmethod
    def _urls(cls, v: list[str]) -> list[str]:
        out = []
        for u in v:
            u = u.strip()
            if not re.match(r"^https://[A-Za-z0-9.\-]+(:\d+)?(/[^\s]*)?$", u) or len(u) > 400:
                raise ValueError(f"docs URLs must be public https URLs: {u[:60]}")
            out.append(u)
        return out


def _root() -> Path:
    return Path(settings.DATA_DIR) / "uploads"


def _dir(upload_id: str) -> Path:
    if not _ID.match(upload_id):
        raise UploadError("invalid upload id")
    return _root() / upload_id


def _read_csv(raw: bytes, name: str) -> pd.DataFrame:
    if not raw:
        raise UploadError(f"{name}: the file is empty")
    if len(raw) > MAX_BYTES:
        raise UploadError(f"{name}: larger than {MAX_BYTES // (1024 * 1024)} MB")
    try:
        df = pd.read_csv(io.BytesIO(raw), nrows=MAX_ROWS + 1, low_memory=False)
    except Exception as e:  # noqa: BLE001 — parser messages are safe to show
        raise UploadError(f"{name}: not a readable CSV ({type(e).__name__})") from None
    if len(df) > MAX_ROWS:
        raise UploadError(f"{name}: more than {MAX_ROWS:,} rows")
    if df.shape[1] > MAX_COLS:
        raise UploadError(f"{name}: more than {MAX_COLS} columns")
    if df.shape[1] < 2:
        raise UploadError(f"{name}: needs at least a target and one feature column")
    df.columns = [str(c).strip() for c in df.columns]
    if len(set(df.columns)) != len(df.columns):
        raise UploadError(f"{name}: duplicate column names")
    return df


def _profile_columns(ref: pd.DataFrame, cur: pd.DataFrame) -> list[dict[str, Any]]:
    cols = []
    for c in ref.columns:
        s = ref[c]
        nun = int(s.nunique(dropna=True))
        numeric = pd.api.types.is_numeric_dtype(s)
        kind = ("numeric" if numeric and nun > 10 else
                "categorical" if nun <= MAX_CATEGORY_LEVELS else
                "id_like" if nun >= 0.9 * len(s) else "high_cardinality")
        cols.append({"name": c, "kind": kind, "unique": nun, "null_rate": round(float(s.isna().mean()), 4),
                     "in_current": c in cur.columns,
                     "values": sorted(map(str, s.dropna().unique()))[:MAX_CATEGORY_LEVELS] if nun <= MAX_CATEGORY_LEVELS else []})
    return cols


def inspect_and_store(tenant: str, ref_raw: bytes, cur_raw: bytes) -> dict[str, Any]:
    """Validate and save the two windows as a draft; return the inferred schema."""
    ref, cur = _read_csv(ref_raw, "reference"), _read_csv(cur_raw, "current")
    if len(ref) < MIN_REFERENCE_ROWS:
        raise UploadError(f"reference: at least {MIN_REFERENCE_ROWS} labelled rows are needed to rebuild the model")
    upload_id = uuid.uuid4().hex[:12]
    d = _dir(upload_id)
    d.mkdir(parents=True, exist_ok=False)
    ref.to_csv(d / "reference.csv", index=False)
    cur.to_csv(d / "current.csv", index=False)
    cols = _profile_columns(ref, cur)
    meta = {"upload_id": upload_id, "tenant": tenant, "created_at": datetime.now(timezone.utc).isoformat(),
            "rows": {"reference": len(ref), "current": len(cur)}, "columns": cols, "spec": None}
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return _public(meta)


def _public(meta: dict) -> dict[str, Any]:
    targets = [c["name"] for c in meta["columns"] if c["unique"] == 2]
    segments = [c["name"] for c in meta["columns"] if c["kind"] == "categorical" and 2 <= c["unique"] <= 30]
    return {"upload_id": meta["upload_id"], "rows": meta["rows"], "columns": meta["columns"],
            "target_candidates": targets, "segment_candidates": segments, "configured": meta["spec"] is not None,
            "title": (meta.get("spec") or {}).get("title")}


def _meta(tenant: str, upload_id: str) -> dict:
    p = _dir(upload_id) / "meta.json"
    if not p.exists():
        raise KeyError(upload_id)
    meta = json.loads(p.read_text(encoding="utf-8"))
    if meta["tenant"] != tenant:
        raise KeyError(upload_id)                 # another visitor's upload: indistinguishable from missing
    return meta


def get(tenant: str, upload_id: str) -> dict[str, Any]:
    return _public(_meta(tenant, upload_id))


def configure(tenant: str, upload_id: str, spec: UploadSpec) -> dict[str, Any]:
    """Check the settings against the data and freeze them. Raises UploadError with a
    message written for the user."""
    meta = _meta(tenant, upload_id)
    d = _dir(upload_id)
    ref = pd.read_csv(d / "reference.csv", low_memory=False)
    cur = pd.read_csv(d / "current.csv", low_memory=False)
    if spec.target not in ref.columns:
        raise UploadError(f"target column {spec.target!r} is not in the reference file")
    labels = set(ref[spec.target].dropna().astype(str))
    if len(labels) != 2:
        raise UploadError(f"target {spec.target!r} must have exactly two values in the reference file "
                          f"(found {len(labels)}); Autopilot handles binary classification")
    if spec.positive_label not in labels:
        raise UploadError(f"positive label {spec.positive_label!r} is not one of {sorted(labels)}")
    if spec.segment_column:
        if spec.segment_column not in ref.columns or spec.segment_column == spec.target:
            raise UploadError(f"segment column {spec.segment_column!r} is not a feature in the reference file")
        if spec.segment_value is not None and spec.segment_value not in set(ref[spec.segment_column].dropna().astype(str)):
            raise UploadError(f"segment value {spec.segment_value!r} does not occur in {spec.segment_column!r}")
    kinds = {c["name"]: c["kind"] for c in meta["columns"]}
    excluded = set(spec.exclude_columns) | {spec.target}
    numeric = [c for c, k in kinds.items() if k == "numeric" and c not in excluded]
    categorical = [c for c, k in kinds.items() if k == "categorical" and c not in excluded]
    dropped = [c for c, k in kinds.items() if k in ("id_like", "high_cardinality") and c not in excluded]
    if not numeric + categorical:
        raise UploadError("no usable feature columns (all excluded, ID-like or high-cardinality text)")
    if spec.target not in cur.columns:
        cur[spec.target] = np.nan                  # no labels yet: drift-only investigation
    meta["spec"] = spec.model_dump()
    meta["features"] = {"numeric": numeric, "categorical": categorical, "dropped": dropped}
    meta["labelled_current_rows"] = int(cur[spec.target].notna().sum())
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return {**_public(meta), "features": meta["features"], "labelled_current_rows": meta["labelled_current_rows"],
            "warnings": ([f"Ignored as ID-like or free text: {', '.join(dropped)}"] if dropped else [])
            + ([] if meta["labelled_current_rows"] else
               ["The current file has no labels yet: Autopilot can measure drift but not quality loss."])}


def owner(upload_id: str) -> str | None:
    try:
        return json.loads((_dir(upload_id) / "meta.json").read_text(encoding="utf-8"))["tenant"]
    except (OSError, UploadError, KeyError, ValueError):
        return None


def list_for(tenant: str, limit: int = 20) -> list[dict[str, Any]]:
    root = _root()
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if meta.get("tenant") == tenant:
            out.append(_public(meta))
        if len(out) >= limit:
            break
    return out


def delete(tenant: str, upload_id: str) -> None:
    _meta(tenant, upload_id)
    shutil.rmtree(_dir(upload_id), ignore_errors=True)


def _y(series: pd.Series, positive: str) -> pd.Series:
    return series.map(lambda v: np.nan if pd.isna(v) else float(str(v) == positive))


def load_pack(upload_id: str):
    """Build the IncidentPack the engine investigates."""
    from app.autopilot.scenarios import IncidentPack
    d = _dir(upload_id)
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    spec = meta.get("spec")
    if not spec:
        raise ValueError("upload is not configured yet")
    ref = pd.read_csv(d / "reference.csv", low_memory=False)
    cur = pd.read_csv(d / "current.csv", low_memory=False)
    t = spec["target"]
    if t not in cur.columns:
        cur[t] = np.nan
    ref[t], cur[t] = _y(ref[t], spec["positive_label"]), _y(cur[t], spec["positive_label"])
    ref = ref[ref[t].notna()].reset_index(drop=True)
    f = meta["features"]
    for c in f["categorical"]:
        for df in (ref, cur):
            if c in df.columns:
                df[c] = df[c].where(df[c].isna(), df[c].astype(str))
    trained = spec.get("trained_threshold") or spec["serving_threshold"]
    pipeline = {"model": spec["model_family"], "numeric": f["numeric"], "categorical": f["categorical"],
                "onehot": {"handle_unknown": "ignore", "sparse_output": False}, "threshold": trained,
                "class_weight": None, "optional_features": []}
    seg = None
    if spec.get("segment_column"):
        seg = {"column": spec["segment_column"], "value": spec.get("segment_value"),
               "metric": spec["metric"], "max_drop": spec["segment_max_drop"]}
    env: dict[str, Any] = dict(spec.get("packages_after") or {})
    if spec.get("packages_before"):
        env["previous"] = dict(spec["packages_before"])
    return IncidentPack(
        f"upload:{upload_id}", "pipeline_failure" if spec.get("error_log") else "degradation",
        spec["title"], spec["symptom"], ref, cur, target=t, primary_metric=spec["metric"],
        pipeline=pipeline, serving={"threshold": spec["serving_threshold"]}, environment=env,
        error_log=spec.get("error_log") or None, critical_segment=seg,
        source="upload", notes=spec.get("notes") or "", docs_urls=list(spec.get("docs_urls") or []))
