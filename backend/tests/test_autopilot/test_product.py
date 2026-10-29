"""Capabilities, real-score provenance, retention and monitoring contracts."""
import asyncio
import io
import json
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.autopilot import mlcore, service, sharing, uploads
from app.config import settings

from .test_uploads import _inline, _h, _sample, _upload, _spec, _configured, _wait, BASE


def test_share_signatures_expiry_and_scope(monkeypatch):
    monkeypatch.setattr(settings, "AUTOPILOT_SHARE_SECRET", "test-signing-key-" * 3)
    rid = "a" * 8 + "-" + "b" * 4 + "-" + "c" * 4 + "-" + "d" * 4 + "-" + "e" * 12
    token, _ = sharing.sign(rid)
    assert sharing.resolve(token) == rid
    for bad in (token[:-1] + ("0" if token[-1] != "0" else "1"), token.replace("a", "f", 1), "../../anything"):
        with pytest.raises(ValueError):
            sharing.resolve(bad)
    expired, _ = sharing.sign(rid, lifetime=-1)
    with pytest.raises(ValueError):
        sharing.resolve(expired)


def test_capabilities_are_redacted_from_access_logs_and_metric_labels():
    import logging
    from app.main import _norm_path
    token = 'private-capability'
    path = f"{BASE}/shared/{token}"
    record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0,
                               '%s - "%s %s HTTP/%s" %s', ('127.0.0.1','GET',path,'1.1',200), None)
    sharing.CapabilityLogFilter().filter(record)
    assert token not in record.getMessage()
    assert _norm_path(path) == f"{BASE}/shared/:token"


async def test_shared_run_is_read_only_and_not_a_tenant_credential(client):
    h, stranger = _h(), _h()
    r = await client.post(f"{BASE}/investigations", headers=h, json={"scenario_id": "S03", "planner": "deterministic"})
    rid = r.json()["run_id"]
    state = await _wait(client, h, rid)
    assert state["status"] == "REVIEW_READY"
    assert (await client.post(f"{BASE}/investigations/{rid}/share", headers=stranger)).status_code == 404
    link = (await client.post(f"{BASE}/investigations/{rid}/share", headers=h)).json()
    token = link["token"]
    shared = await client.get(f"{BASE}/shared/{token}", headers=stranger)
    assert shared.status_code == 200 and shared.json()["run_id"] == rid
    assert shared.headers["cache-control"] == "no-store"
    for action in ("next", "cancel", "review", "share"):
        result = await client.post(f"{BASE}/investigations/{rid}/{action}", headers={**stranger, "Authorization": f"Bearer {token}"},
                                   json={"action": "approve", "reviewer": "attacker", "note": "x"})
        assert result.status_code in (401, 404)
    assert (await client.get(f"{BASE}/investigations", headers=stranger)).json()["investigations"] == []


async def test_scores_used_for_threshold_and_final_test(client):
    h = _h()
    ref, cur = await _sample(client, "threshold-change")
    frames = [pd.read_csv(io.BytesIO(raw)) for raw in (ref, cur)]
    rng = np.random.default_rng(27)
    for df in frames:
        # Deliberately different from the reconstructed classifier.
        df["score"] = np.where(df.defaulted == 1, 0.40, 0.10) + rng.uniform(-0.04, 0.04, len(df))
    uid = (await _upload(client, h, *(df.to_csv(index=False).encode() for df in frames))).json()["upload_id"]
    configured = await client.post(f"{BASE}/uploads/{uid}/configure", headers=h, json=_spec(serving_threshold=0.62, trained_threshold=0.3))
    assert configured.status_code == 200
    assert "score" not in configured.json()["features"]["numeric"]
    r = await client.post(f"{BASE}/investigations", headers=h, json={"scenario_id": f"upload:{uid}", "planner": "deterministic"})
    st = await _wait(client, h, r.json()["run_id"])
    assert st["scenario"]["prediction_source"] == "user_scores"
    assert st["decision"]["decision"] == "CORRECTION_VERIFIED", st.get("error")
    assert st["verification"]["prediction_sources"] == {"incumbent": "user_scores", "candidate": "user_scores"}
    assert st["verification"]["metrics"]["final_test"]["candidate"]["f1"] == 1
    assert len(st["final_test_accesses"]) == 1


async def test_invalid_scores_and_unknown_outcomes_are_rejected(client):
    h = _h()
    ref, cur = await _sample(client)
    r = pd.read_csv(io.BytesIO(ref)); c = pd.read_csv(io.BytesIO(cur))
    r["score"] = 0.2
    uid = (await _upload(client, h, r.to_csv(index=False).encode(), cur)).json()["upload_id"]
    bad = await client.post(f"{BASE}/uploads/{uid}/configure", headers=h, json=_spec())
    assert bad.status_code == 422 and "score" in bad.json()["detail"]
    c["score"] = 1.1
    uid = (await _upload(client, h, r.to_csv(index=False).encode(), c.to_csv(index=False).encode())).json()["upload_id"]
    assert (await client.post(f"{BASE}/uploads/{uid}/configure", headers=h, json=_spec())).status_code == 422


async def test_partial_numeric_outcomes_and_numeric_ids(client):
    h = _h(); ref, cur = await _sample(client)
    r = pd.read_csv(io.BytesIO(ref)); c = pd.read_csv(io.BytesIO(cur))
    r["record_id"] = np.arange(len(r)); c["record_id"] = np.arange(len(c))
    c.loc[c.index[::3], "defaulted"] = np.nan
    uid = (await _upload(client, h, r.to_csv(index=False).encode(), c.to_csv(index=False).encode())).json()["upload_id"]
    result = await client.post(f"{BASE}/uploads/{uid}/configure", headers=h, json=_spec())
    assert result.status_code == 200, result.text
    assert "record_id" in result.json()["features"]["dropped"]
    pack = uploads.load_pack(uid)
    assert int(pack.current.defaulted.sum()) == int(c.defaulted.sum())
    assert pack.current.defaulted.isna().sum() == c.defaulted.isna().sum()


def test_public_data_incident_generalizes_without_a_network_download(run_investigation):
    state, _ = run_investigation("A01", seed=101, research="off")
    assert state["decision"]["decision"] == "CORRECTION_VERIFIED"
    assert state["scenario"]["source"] == "public_dataset"
    assert state["verification"]["metrics"]["critical_segment"]["segment"].startswith("sex=")


@pytest.mark.parametrize("family", ["logistic_regression", "hist_gradient_boosting", "random_forest", "xgboost", "lightgbm"])
def test_model_family_contract(family):
    if family in ("xgboost", "lightgbm"):
        pytest.importorskip(family)
    df = pd.DataFrame({"x": np.linspace(-1, 1, 80), "group": ["a", "b"] * 40})
    model = mlcore.build_pipeline({"model": family, "numeric": ["x"], "categorical": ["group"],
                                   "onehot": {"sparse_output": False, "handle_unknown": "ignore"}})
    model.fit(df, (df.x > 0).astype(int))
    scores = model.predict_proba(df)
    assert scores.shape == (80, 2) and np.isfinite(scores).all()


async def test_retention_deletes_derived_reports_and_preserves_active_runs(client):
    from pathlib import Path
    h = _h(); uid = await _configured(client, h)
    r = await client.post(f"{BASE}/investigations", headers=h, json={"scenario_id": f"upload:{uid}", "planner": "deterministic"})
    rid = r.json()["run_id"]; await _wait(client, h, rid)
    # Let the completed driver's finally block release its local slot.
    for _ in range(20):
        if rid not in service._active: break
        await asyncio.sleep(0.02)
    meta_path = uploads._dir(uid) / "meta.json"
    meta = json.loads(meta_path.read_text()); meta["created_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    meta_path.write_text(json.dumps(meta))
    assert await service.cleanup_uploads() == 1
    assert (await client.get(f"{BASE}/investigations/{rid}", headers=h)).status_code == 404
    assert not (Path(settings.DATA_DIR) / "autopilot" / rid).exists()


async def test_alert_adapter_auth_tenancy_idempotency_and_slack_output(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTOPILOT_ALERT_TOKEN", "test-alert-token")
    h = _h(); uid = await _configured(client, h)
    body = {"upload_id": uid, "source": "evidently", "alert_id": "quality-42"}
    assert (await client.post(f"{BASE}/hooks/alert", headers=h, json=body)).status_code == 401
    auth = {**h, "X-Alert-Token": "test-alert-token"}
    first = await client.post(f"{BASE}/hooks/alert", headers=auth, json=body)
    second = await client.post(f"{BASE}/hooks/alert", headers=auth, json=body)
    assert first.json()["run_id"] == second.json()["run_id"]
    assert (await client.post(f"{BASE}/hooks/alert", headers={**_h(), "X-Alert-Token": "test-alert-token"}, json=body)).status_code == 404
    rid = first.json()["run_id"]; await _wait(client, h, rid)
    summary = (await client.get(f"{BASE}/investigations/{rid}/summary", headers=h)).json()
    assert summary["blocks"][0]["text"]["type"] == "plain_text"
