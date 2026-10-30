"""Stage-specific MLOps benchmark contract for 25_GREAT_EXPECTATIONS__GREAT_EXPECTATIONS / MODEL_SERVING."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Mapping, Optional


STAGE = 'MODEL_SERVING'
REPO_KEY = '25_GREAT_EXPECTATIONS__GREAT_EXPECTATIONS'
REQUIRED_FIELDS = ('request_id', 'timestamp', 'latency_ms', 'prediction', 'model_version', 'status_code')
NUMERIC_FIELDS = ('latency_ms', 'prediction', 'status_code')
TIMESTAMP_FIELD = 'timestamp'
ID_FIELD = 'request_id'
PRIMARY_METRIC_FIELD = 'latency_ms'
EXTRA_CONTRACT = 'serving_health'
DEFAULT_THRESHOLDS = {'max_age_minutes': 40, 'max_duplicate_rate': 0.005, 'max_missing_rate': 0.0, 'max_drift_ratio': 0.14, 'max_feature_skew': 0.05, 'min_accuracy': 0.8, 'min_roc_auc': 0.74, 'max_loss_regression': 0.03, 'max_p95_latency_ms': 155, 'max_error_rate': 0.01, 'max_alert_rate': 0.24, 'min_quality_score': 0.8}


def describe_contract() -> Dict[str, Any]:
    return {
        "stage": STAGE,
        "repo_key": REPO_KEY,
        "required_fields": list(REQUIRED_FIELDS),
        "numeric_fields": list(NUMERIC_FIELDS),
        "timestamp_field": TIMESTAMP_FIELD,
        "id_field": ID_FIELD,
        "thresholds": dict(DEFAULT_THRESHOLDS),
        "report_fields": [
            "passed",
            "metrics",
            "violations",
            "recommended_action",
            "artifact_path",
            "decision_id",
            "lineage",
        ],
    }


def summarize_records(records: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = [dict(record) for record in records]
    return {
        "stage": STAGE,
        "repo_key": REPO_KEY,
        "total_records": len(rows),
        "entity_count": len({str(row.get(ID_FIELD)) for row in rows if row.get(ID_FIELD) not in (None, "")}),
        "required_fields": list(REQUIRED_FIELDS),
    }


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _float_or_none(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _violation(code: str, message: str, *, severity: str = "high", field: str = "", record_id: Any = None) -> Dict[str, Any]:
    item = {"code": code, "message": message, "severity": severity}
    if field:
        item["field"] = field
    if record_id is not None:
        item["record_id"] = str(record_id)
    return item


def _decision_id(rows: List[Dict[str, Any]], metrics: Mapping[str, Any], violations: List[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        {
            "stage": STAGE,
            "repo_key": REPO_KEY,
            "rows": rows,
            "metrics": metrics,
            "violations": violations,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _write_json(report: Dict[str, Any], output_path: Any) -> Optional[str]:
    if output_path is None:
        return None
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(report)
    payload["artifact_path"] = str(path)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def _recommended_action(violations: List[Mapping[str, Any]]) -> str:
    codes = {str(item.get("code")) for item in violations}
    if not codes:
        return "none"
    if codes & {"drift_regression", "feature_skew", "loss_regression", "serving_latency_breach", "observability_drift"}:
        return "retrain"
    if codes & {"release_not_approved", "invalid_artifact_hash", "missing_stage", "stage_failed", "stage_order_invalid"}:
        return "rollback_review"
    if codes & {"missing_required_field", "null_required_field", "invalid_numeric", "invalid_timestamp", "stale_record", "duplicate_identifier"}:
        return "investigate_data_quality"
    return "block_release"


def evaluate_serving_window(
    records: Iterable[Mapping[str, Any]],
    output_path: Any = None,
    *,
    baseline: Optional[Iterable[Mapping[str, Any]]] = None,
    thresholds: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    rows = [dict(record) for record in records]
    active = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        active.update({str(key): float(value) for key, value in thresholds.items()})

    metrics: Dict[str, Any] = summarize_records(rows)
    metrics.update({
        "missing_required_count": 0,
        "null_required_count": 0,
        "invalid_numeric_count": 0,
        "invalid_timestamp_count": 0,
        "duplicate_count": 0,
        "stale_count": 0,
        "quality_pass_rate": 0.0,
        "freshness_breach_rate": 0.0,
        "duplicate_rate": 0.0,
        "drift_ratio": 0.0,
    })
    violations: List[Dict[str, Any]] = []

    if 'oracle' == "baseline":
        violations.append(_violation("not_implemented", "Stage contract is not implemented.", severity="critical"))
        report = {
            "passed": False,
            "metrics": metrics,
            "violations": violations,
            "recommended_action": "block_release",
            "artifact_path": None,
            "decision_id": _decision_id(rows, metrics, violations),
            "lineage": {"stage": STAGE, "repo_key": REPO_KEY, "record_count": len(rows)},
        }
        report["artifact_path"] = _write_json(report, output_path)
        return report

    seen: set[str] = set()
    now = datetime.now(timezone.utc)
    metric_values: List[float] = []
    invalid_rows = 0

    for index, row in enumerate(rows):
        record_id = row.get(ID_FIELD, f"row-{index}")
        bad_row = False
        for field in REQUIRED_FIELDS:
            if field not in row:
                metrics["missing_required_count"] += 1
                bad_row = True
                violations.append(_violation("missing_required_field", f"Missing required field {field}.", field=field, record_id=record_id))
            elif row.get(field) in (None, ""):
                metrics["null_required_count"] += 1
                bad_row = True
                violations.append(_violation("null_required_field", f"Field {field} cannot be null or blank.", field=field, record_id=record_id))

        rid = str(record_id)
        if rid in seen and 'oracle' != "skip_duplicates_and_drift":
            metrics["duplicate_count"] += 1
            bad_row = True
            violations.append(_violation("duplicate_identifier", f"Duplicate {ID_FIELD} {rid}.", field=ID_FIELD, record_id=record_id))
        seen.add(rid)

        parsed = _parse_ts(row.get(TIMESTAMP_FIELD))
        if parsed is None:
            metrics["invalid_timestamp_count"] += 1
            bad_row = True
            violations.append(_violation("invalid_timestamp", f"{TIMESTAMP_FIELD} must be ISO-8601 parseable.", field=TIMESTAMP_FIELD, record_id=record_id))
        else:
            age_minutes = max(0.0, (now - parsed).total_seconds() / 60.0)
            if age_minutes > float(active["max_age_minutes"]):
                metrics["stale_count"] += 1
                bad_row = True
                violations.append(_violation("stale_record", f"Record age {age_minutes:.1f} minutes exceeds threshold.", field=TIMESTAMP_FIELD, record_id=record_id))

        for field in NUMERIC_FIELDS:
            numeric_value = _float_or_none(row.get(field))
            if numeric_value is None:
                metrics["invalid_numeric_count"] += 1
                bad_row = True
                violations.append(_violation("invalid_numeric", f"{field} must be numeric.", field=field, record_id=record_id))
            elif field == PRIMARY_METRIC_FIELD:
                metric_values.append(numeric_value)

        if bad_row:
            invalid_rows += 1

    total = max(len(rows), 1)
    metrics["duplicate_rate"] = round(metrics["duplicate_count"] / total, 6)
    metrics["freshness_breach_rate"] = round(metrics["stale_count"] / total, 6)
    metrics["quality_pass_rate"] = round((len(rows) - invalid_rows) / total, 6) if rows else 0.0
    if metric_values:
        metrics["mean_primary_metric"] = round(mean(metric_values), 6)

    _apply_extra_checks(rows, baseline, active, metrics, violations, 'oracle')

    passed = bool(rows) and not violations
    if not rows:
        violations.append(_violation("no_records", "At least one record is required.", severity="critical"))
        passed = False

    report = {
        "passed": passed,
        "metrics": metrics,
        "violations": violations,
        "recommended_action": _recommended_action(violations),
        "artifact_path": None,
        "decision_id": _decision_id(rows, metrics, violations),
        "lineage": {
            "stage": STAGE,
            "repo_key": REPO_KEY,
            "record_count": len(rows),
            "required_fields": list(REQUIRED_FIELDS),
            "thresholds": active,
        },
    }
    report["artifact_path"] = _write_json(report, output_path)
    return report


def _apply_extra_checks(
    rows: List[Dict[str, Any]],
    baseline: Optional[Iterable[Mapping[str, Any]]],
    active: Mapping[str, Any],
    metrics: Dict[str, Any],
    violations: List[Dict[str, Any]],
    mode: str,
) -> None:
    baseline_rows = [dict(record) for record in baseline] if baseline is not None else []
    if EXTRA_CONTRACT == "target_balance":
        labels = [row.get("label") for row in rows if row.get("label") not in (None, "")]
        metrics["target_positive_rate"] = round(sum(1 for value in labels if str(value) in {"1", "true", "True"}) / max(len(labels), 1), 6)
        if labels and len({str(value) for value in labels}) < 2:
            violations.append(_violation("single_class_target", "Label distribution must include both classes.", field="label", severity="critical"))
        _baseline_drift(rows, baseline_rows, active, metrics, violations, mode)
    elif EXTRA_CONTRACT == "feature_skew":
        skews = []
        versions = {str(row.get("feature_version")) for row in rows if row.get("feature_version") not in (None, "")}
        for row in rows:
            offline = _float_or_none(row.get("offline_value"))
            online = _float_or_none(row.get("online_value"))
            if offline is not None and online is not None:
                skews.append(abs(online - offline) / max(abs(offline), 1.0))
        metrics["max_feature_skew"] = round(max(skews) if skews else 0.0, 6)
        if mode != "skip_skew_and_version" and metrics["max_feature_skew"] > float(active["max_feature_skew"]):
            violations.append(_violation("feature_skew", "Online feature values diverge from offline materialization.", severity="critical"))
        if mode != "skip_skew_and_version" and len(versions) > 1:
            violations.append(_violation("mixed_feature_versions", "A feature batch must use one feature_version.", field="feature_version"))
    elif EXTRA_CONTRACT == "training_quality":
        accuracies = [_float_or_none(row.get("accuracy")) for row in rows]
        aucs = [_float_or_none(row.get("roc_auc")) for row in rows]
        losses = [_float_or_none(row.get("loss")) for row in rows]
        metrics["best_accuracy"] = round(max([value for value in accuracies if value is not None] or [0.0]), 6)
        metrics["best_roc_auc"] = round(max([value for value in aucs if value is not None] or [0.0]), 6)
        metrics["min_loss"] = round(min([value for value in losses if value is not None] or [999.0]), 6)
        if metrics["best_accuracy"] < float(active["min_accuracy"]):
            violations.append(_violation("accuracy_below_threshold", "Candidate accuracy is below release threshold.", field="accuracy", severity="critical"))
        if metrics["best_roc_auc"] < float(active["min_roc_auc"]):
            violations.append(_violation("roc_auc_below_threshold", "Candidate ROC AUC is below release threshold.", field="roc_auc", severity="critical"))
        if mode != "skip_loss_regression":
            base_losses = [_float_or_none(row.get("loss")) for row in baseline_rows]
            base_losses = [value for value in base_losses if value is not None]
            if base_losses and losses and metrics["min_loss"] > min(base_losses) + float(active["max_loss_regression"]):
                violations.append(_violation("loss_regression", "Loss regressed versus baseline.", field="loss", severity="critical"))
    elif EXTRA_CONTRACT == "serving_health":
        latencies = sorted([_float_or_none(row.get("latency_ms")) for row in rows if _float_or_none(row.get("latency_ms")) is not None])
        statuses = [_float_or_none(row.get("status_code")) for row in rows]
        predictions = [str(row.get("prediction")) for row in rows if row.get("prediction") not in (None, "")]
        p95_index = max(0, int(len(latencies) * 0.95) - 1)
        metrics["p95_latency_ms"] = round(latencies[p95_index], 6) if latencies else 0.0
        metrics["error_rate"] = round(sum(1 for status in statuses if status is not None and status >= 500) / max(len(statuses), 1), 6)
        metrics["prediction_class_count"] = len(set(predictions))
        if mode != "skip_latency_and_errors" and metrics["p95_latency_ms"] > float(active["max_p95_latency_ms"]):
            violations.append(_violation("serving_latency_breach", "p95 latency exceeds serving SLO.", field="latency_ms", severity="critical"))
        if mode != "skip_latency_and_errors" and metrics["error_rate"] > float(active["max_error_rate"]):
            violations.append(_violation("serving_error_rate", "5xx error rate exceeds SLO.", field="status_code", severity="critical"))
        if predictions and len(set(predictions)) < 2:
            violations.append(_violation("single_class_predictions", "Serving window must not collapse to one prediction class.", field="prediction"))
    elif EXTRA_CONTRACT == "monitoring_drift":
        values = [_float_or_none(row.get("value")) for row in rows]
        values = [value for value in values if value is not None]
        alert_rate = sum(1 for row in rows if str(row.get("severity", "")).lower() in {"warning", "critical"}) / max(len(rows), 1)
        metrics["alert_rate"] = round(alert_rate, 6)
        if alert_rate > float(active["max_alert_rate"]):
            violations.append(_violation("alert_rate_breach", "Too many monitoring events are warnings or critical.", field="severity"))
        if mode != "skip_drift":
            _baseline_drift(rows, baseline_rows, active, metrics, violations, mode, code="observability_drift")
    elif EXTRA_CONTRACT == "release_manifest":
        types = {str(row.get("artifact_type")) for row in rows}
        required_types = {"model", "data", "config"}
        metrics["artifact_type_count"] = len(types)
        missing = sorted(required_types - types)
        if missing:
            violations.append(_violation("missing_artifact_type", "Release manifest is missing required artifact types: " + ",".join(missing), field="artifact_type", severity="critical"))
        for row in rows:
            sha = str(row.get("sha256", ""))
            if mode != "skip_hash_and_approval" and (len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha.lower())):
                violations.append(_violation("invalid_artifact_hash", "sha256 must be a 64-character hex digest.", field="sha256", record_id=row.get(ID_FIELD), severity="critical"))
            if mode != "skip_hash_and_approval" and row.get("approved") is not True:
                violations.append(_violation("release_not_approved", "All release artifacts require explicit approval.", field="approved", record_id=row.get(ID_FIELD), severity="critical"))
    elif EXTRA_CONTRACT == "stage_flow":
        required_order = ["data_pipeline", "feature_engineering", "model_training", "model_serving", "monitoring_observability", "cicd_governance"]
        seen_order = [str(row.get("stage")) for row in rows]
        metrics["stage_count"] = len(set(seen_order))
        missing = [name for name in required_order if name not in seen_order]
        if mode != "skip_stage_sequence":
            if missing:
                violations.append(_violation("missing_stage", "Missing stages: " + ",".join(missing), field="stage", severity="critical"))
            positions = [seen_order.index(name) for name in required_order if name in seen_order]
            if positions != sorted(positions):
                violations.append(_violation("stage_order_invalid", "MLOps stages must be reported in release order.", field="stage", severity="critical"))
        for row in rows:
            if str(row.get("status")) != "passed":
                violations.append(_violation("stage_failed", "All stages must pass before release.", field="status", record_id=row.get("stage"), severity="critical"))
            score = _float_or_none(row.get("quality_score"))
            if score is not None and mode != "skip_stage_sequence" and score < float(active["min_quality_score"]):
                violations.append(_violation("stage_quality_below_threshold", "Stage quality score is below threshold.", field="quality_score", record_id=row.get("stage"), severity="critical"))


def _baseline_drift(
    rows: List[Dict[str, Any]],
    baseline_rows: List[Dict[str, Any]],
    active: Mapping[str, Any],
    metrics: Dict[str, Any],
    violations: List[Dict[str, Any]],
    mode: str,
    *,
    code: str = "drift_regression",
) -> None:
    if mode == "skip_duplicates_and_drift" or not baseline_rows:
        return
    current = [_float_or_none(row.get(PRIMARY_METRIC_FIELD)) for row in rows]
    base = [_float_or_none(row.get(PRIMARY_METRIC_FIELD)) for row in baseline_rows]
    current = [value for value in current if value is not None]
    base = [value for value in base if value is not None]
    if not current or not base:
        return
    base_mean = mean(base)
    current_mean = mean(current)
    ratio = abs(current_mean - base_mean) / max(abs(base_mean), 1.0)
    metrics["drift_ratio"] = round(ratio, 6)
    if ratio > float(active["max_drift_ratio"]):
        violations.append(_violation(code, "Current window drifted from baseline.", field=PRIMARY_METRIC_FIELD, severity="critical"))
