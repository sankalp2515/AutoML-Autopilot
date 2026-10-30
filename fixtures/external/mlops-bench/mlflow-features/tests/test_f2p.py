from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone


module = importlib.import_module('mlops_bench_contracts.feature_parity')
gate = module.build_feature_parity_report


def _ts(minutes_old=1):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_old)).isoformat()


def _valid_records():
    if 'feature_skew' == "target_balance":
        return [
            {"dataset_id": "r1", "event_ts": _ts(), "entity_id": "u1", "feature_value": 10.0, "label": 0},
            {"dataset_id": "r2", "event_ts": _ts(), "entity_id": "u2", "feature_value": 11.0, "label": 1},
            {"dataset_id": "r3", "event_ts": _ts(), "entity_id": "u3", "feature_value": 10.5, "label": 0},
        ]
    if 'feature_skew' == "feature_skew":
        return [
            {"entity_id": "u1", "event_ts": _ts(), "offline_value": 10.0, "online_value": 10.01, "feature_version": "v7"},
            {"entity_id": "u2", "event_ts": _ts(), "offline_value": 20.0, "online_value": 20.01, "feature_version": "v7"},
        ]
    if 'feature_skew' == "training_quality":
        return [
            {"run_id": "run-1", "started_at": _ts(), "accuracy": 0.91, "roc_auc": 0.88, "loss": 0.22, "model_uri": "models/a.pkl"},
            {"run_id": "run-2", "started_at": _ts(), "accuracy": 0.86, "roc_auc": 0.82, "loss": 0.25, "model_uri": "models/b.pkl"},
        ]
    if 'feature_skew' == "serving_health":
        return [
            {"request_id": "q1", "timestamp": _ts(), "latency_ms": 45, "prediction": 0, "model_version": "v3", "status_code": 200},
            {"request_id": "q2", "timestamp": _ts(), "latency_ms": 51, "prediction": 1, "model_version": "v3", "status_code": 200},
            {"request_id": "q3", "timestamp": _ts(), "latency_ms": 49, "prediction": 0, "model_version": "v3", "status_code": 200},
        ]
    if 'feature_skew' == "monitoring_drift":
        return [
            {"metric_name": "accuracy", "timestamp": _ts(), "value": 0.91, "segment": "all", "severity": "info"},
            {"metric_name": "latency", "timestamp": _ts(), "value": 72.0, "segment": "api", "severity": "info"},
        ]
    if 'feature_skew' == "release_manifest":
        return [
            {"artifact_id": "model", "artifact_type": "model", "sha256": "a" * 64, "approved": True, "created_at": _ts()},
            {"artifact_id": "data", "artifact_type": "data", "sha256": "b" * 64, "approved": True, "created_at": _ts()},
            {"artifact_id": "config", "artifact_type": "config", "sha256": "c" * 64, "approved": True, "created_at": _ts()},
        ]
    return [
        {"stage": "data_pipeline", "status": "passed", "started_at": _ts(), "artifact_uri": "artifacts/data.json", "quality_score": 0.94},
        {"stage": "feature_engineering", "status": "passed", "started_at": _ts(), "artifact_uri": "artifacts/features.json", "quality_score": 0.93},
        {"stage": "model_training", "status": "passed", "started_at": _ts(), "artifact_uri": "artifacts/model.json", "quality_score": 0.92},
        {"stage": "model_serving", "status": "passed", "started_at": _ts(), "artifact_uri": "artifacts/serving.json", "quality_score": 0.91},
        {"stage": "monitoring_observability", "status": "passed", "started_at": _ts(), "artifact_uri": "artifacts/monitoring.json", "quality_score": 0.9},
        {"stage": "cicd_governance", "status": "passed", "started_at": _ts(), "artifact_uri": "artifacts/release.json", "quality_score": 0.89},
    ]


def _codes(report):
    return {item["code"] for item in report["violations"]}


def test_valid_records_pass_and_write_matching_artifact(tmp_path):
    output = tmp_path / "nested" / "release_decision.json"
    rows = _valid_records()
    report = gate(rows, output_path=output)

    assert report["passed"] is True
    assert report["recommended_action"] == "none"
    assert report["violations"] == []
    assert report["metrics"]["total_records"] == len(rows)
    assert report["metrics"]["quality_pass_rate"] == 1.0
    assert len(report["decision_id"]) >= 16
    assert output.exists()
    saved = json.loads(output.read_text())
    assert saved == report
    assert saved["lineage"]["stage"] == module.STAGE


def test_missing_required_field_fails_with_machine_readable_violation(tmp_path):
    rows = _valid_records()
    rows[0].pop('entity_id')

    report = gate(rows, output_path=tmp_path / "missing.json")
    codes = _codes(report)

    assert report["passed"] is False
    assert "missing_required_field" in codes
    assert report["recommended_action"] in {"investigate_data_quality", "rollback_review"}
    assert report["metrics"]["missing_required_count"] >= 1
    assert all("severity" in item for item in report["violations"])
    assert any("record_id" in item for item in report["violations"])


def test_duplicate_and_stale_records_are_blocked(tmp_path):
    rows = _valid_records()
    rows[1][module.ID_FIELD] = rows[0][module.ID_FIELD]
    rows[1][module.TIMESTAMP_FIELD] = _ts(minutes_old=70)

    report = gate(rows, output_path=tmp_path / "freshness.json")
    codes = _codes(report)

    assert report["passed"] is False
    assert "duplicate_identifier" in codes
    assert "stale_record" in codes
    assert report["metrics"]["duplicate_rate"] > 0
    assert report["metrics"]["freshness_breach_rate"] > 0
