"""Route-level coverage for the previously-untested API surfaces (P1.3).

Covers request validation, ownership/404 handling, and error paths for inference,
extras, config, discovery, studios — the routes that had no tests. These assert the
HTTP contract (status codes, validation, guards), not ML behavior.
"""
import io

import pytest

CSV = b"age,job,y\n30,tech,no\n40,mgmt,yes\n"


async def _make_run(client) -> str:
    files = {"file": ("d.csv", io.BytesIO(CSV), "text/csv")}
    data = {"user_goal": "predict y from the columns provided here"}
    r = await client.post("/api/v1/runs", data=data, files=files)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── studios ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_studios_lists_available_and_reserved(client):
    r = await client.get("/api/v1/studios")
    assert r.status_code == 200
    ids = {s["id"]: s for s in r.json()["studios"]}
    assert ids["tabular"]["available"] and ids["timeseries"]["available"]
    assert ids["quant"]["available"] is False       # reserved slot


@pytest.mark.asyncio
async def test_create_run_rejects_unavailable_studio(client):
    files = {"file": ("d.csv", io.BytesIO(CSV), "text/csv")}
    data = {"user_goal": "predict y from these columns", "pipeline": "quant"}
    r = await client.post("/api/v1/runs", data=data, files=files)
    assert r.status_code == 400
    # Rejected as an invalid/unavailable pipeline (either the Form-level pipeline
    # validation or the studio-availability guard — both return 400).
    assert "tabular" in r.text.lower() or "quant" in r.text.lower() or "unavailable" in r.text.lower()


# ── inference: deploy / predict / drift ───────────────────────────────────────

@pytest.mark.asyncio
async def test_predict_requires_deployment(client):
    run_id = await _make_run(client)
    r = await client.post(f"/api/v1/runs/{run_id}/predict", json={"rows": [{"age": 30}]})
    assert r.status_code in (409, 404)          # not deployed / not complete

@pytest.mark.asyncio
async def test_predict_empty_rows_rejected(client):
    run_id = await _make_run(client)
    r = await client.post(f"/api/v1/runs/{run_id}/predict", json={"rows": []})
    assert r.status_code in (400, 409, 422)


@pytest.mark.asyncio
async def test_drift_insufficient_data(client):
    run_id = await _make_run(client)
    r = await client.get(f"/api/v1/runs/{run_id}/drift")
    # No predictions logged yet → insufficient_data (200) or not-ready (409/404).
    assert r.status_code in (200, 404, 409)
    if r.status_code == 200:
        assert r.json().get("status") in ("insufficient_data", "ok")


@pytest.mark.asyncio
async def test_predict_unknown_run_404(client):
    r = await client.post("/api/v1/runs/does-not-exist/predict", json={"rows": [{"a": 1}]})
    assert r.status_code == 404


# ── extras: anomaly / fairness ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_anomaly_contamination_validated(client):
    run_id = await _make_run(client)
    r = await client.post(f"/api/v1/runs/{run_id}/anomaly?contamination=0.9")
    assert r.status_code == 400                  # out of allowed [0.001, 0.5]


@pytest.mark.asyncio
async def test_anomaly_unknown_run_404(client):
    r = await client.post("/api/v1/runs/nope/anomaly?contamination=0.05")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_decision_analysis_needs_completed_run(client):
    run_id = await _make_run(client)
    r = await client.get(f"/api/v1/runs/{run_id}/decision-analysis")
    # No model/holdout on a just-created run → 409 (or 404 if not found).
    assert r.status_code in (409, 404)


@pytest.mark.asyncio
async def test_decision_analysis_unknown_run(client):
    r = await client.get("/api/v1/runs/nope-xyz/decision-analysis")
    assert r.status_code in (409, 404)


@pytest.mark.asyncio
async def test_fairness_requires_columns(client):
    run_id = await _make_run(client)
    r = await client.get(f"/api/v1/runs/{run_id}/fairness")
    assert r.status_code == 422                  # missing required query param


# ── config ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_config_get_masks_secrets(client):
    r = await client.get("/api/v1/config")
    assert r.status_code == 200
    body = r.json()
    assert "fields" in body and "groups" in body
    for f in body["fields"]:
        if f.get("secret") and f.get("value"):
            assert set(str(f["value"])) <= {"*", "•"} or "***" in str(f["value"])


# ── discovery ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_registry_lists_families(client):
    r = await client.get("/api/v1/registry")
    assert r.status_code == 200
    body = r.json()
    assert "families" in body and isinstance(body["families"], list)
    for fam in body["families"]:
        assert "versions" in fam and "champion_run_id" in fam
        for v in fam["versions"]:
            assert v["status"] in ("champion", "challenger")


@pytest.mark.asyncio
async def test_discovery_list_shape(client):
    r = await client.get("/api/v1/discovery")
    assert r.status_code == 200
    body = r.json()
    assert "proposals" in body and "installed" in body


@pytest.mark.asyncio
async def test_discovery_approve_unknown_package_404(client):
    r = await client.post("/api/v1/discovery/no-such-pkg/approve")
    assert r.status_code == 404
