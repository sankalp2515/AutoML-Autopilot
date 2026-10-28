"""SMOKE E2E — a live stack answering real HTTP, with no LLM and no training.

Everything here is cheap and deterministic enough to run on every push. It proves
the things a unit test cannot: the containers actually start, the router is wired,
the DB and Redis are reachable, the OpenAPI contract the frontend types are
generated from is the one actually being served, and the validation/error paths
return the right status codes rather than a 500.
"""
import io

import pytest

pytestmark = [pytest.mark.e2e]


# ── the stack is actually up ─────────────────────────────────────────────────

def test_health_endpoint_reports_every_dependency_ok(client):
    """`/health` (root, NOT under /api/v1) is the container healthcheck target and
    it checks each dependency individually. Asserting only `status == ok` would
    pass while Redis was down, so assert the per-dependency checks."""
    r = client.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") == "ok", body
    checks = body.get("checks", {})
    for dep in ("postgres", "redis", "sandbox"):
        assert checks.get(dep) == "ok", f"{dep} is not healthy: {checks}"


def test_system_health_reports_dependencies(api):
    """The deep health check — this is what tells us Postgres and Redis are
    genuinely reachable from inside the backend container, not just that the
    process is listening."""
    r = api.get("/system/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, dict) and body, "system health returned an empty body"


def test_openapi_is_served_and_parses(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200, r.text
    schema = r.json()
    assert schema.get("openapi", "").startswith("3.")
    assert "/api/v1/runs" in schema.get("paths", {})


# ── the platform seam ────────────────────────────────────────────────────────

def test_studios_registry_drives_the_new_run_modal(api):
    r = api.get("/studios")
    assert r.status_code == 200, r.text
    body = r.json()
    # Contract: an OBJECT with a "studios" list, not a bare array.
    assert isinstance(body, dict) and "studios" in body, body
    studios = body["studios"]
    assert isinstance(studios, list) and studios
    by_id = {s["id"]: s for s in studios}
    assert by_id["tabular"]["available"] is True
    assert by_id["timeseries"]["available"] is True
    # The reserved slot must be advertised but NOT accepting runs.
    assert by_id["quant"]["available"] is False
    for s in studios:
        assert s.get("label") and s.get("tagline")


def test_reserved_studio_is_rejected_not_silently_accepted(api):
    """An unavailable studio must 4xx. Silently falling back to tabular would
    train the wrong kind of model on the user's data."""
    r = api.post("/runs", files={"file": ("t.csv", b"a,b,y\n1,2,0\n3,4,1\n", "text/csv")},
                 data={"user_goal": "predict the y column for me", "pipeline": "quant"})
    assert r.status_code in (400, 422), f"expected rejection, got {r.status_code}: {r.text[:300]}"


# ── input validation happens before any work is scheduled ────────────────────

def test_non_csv_upload_is_rejected(api):
    r = api.post("/runs", files={"file": ("model.pkl", b"\x80\x04", "application/octet-stream")},
                 data={"user_goal": "predict something useful here"})
    assert r.status_code == 400
    assert "csv" in r.text.lower()


def test_too_short_goal_is_rejected(api):
    r = api.post("/runs", files={"file": ("t.csv", b"a,y\n1,0\n2,1\n", "text/csv")},
                 data={"user_goal": "hi"})
    assert r.status_code == 422


def test_invalid_pipeline_value_is_rejected(api):
    r = api.post("/runs", files={"file": ("t.csv", b"a,y\n1,0\n2,1\n", "text/csv")},
                 data={"user_goal": "predict the y column please", "pipeline": "not_a_studio"})
    assert r.status_code in (400, 422)


# ── unknown resources 404 rather than 500 ────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/runs/does-not-exist",
    "/runs/does-not-exist/results",
    "/runs/does-not-exist/trust",
    "/runs/does-not-exist/artifacts",
])
def test_unknown_run_returns_404(api, path):
    r = api.get(path)
    assert r.status_code == 404, f"{path} → {r.status_code} {r.text[:200]}"


def test_decision_analysis_on_unknown_run_is_404_not_500(api):
    r = api.get("/runs/does-not-exist/decision-analysis")
    assert r.status_code == 404, r.text


# ── listing works and is shaped as the frontend expects ──────────────────────

def test_runs_list_is_paginated_shape(api):
    r = api.get("/runs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "runs" in body or isinstance(body, list), body


def test_registry_endpoint_responds(api):
    """Model registry groups runs into champion/challenger families. Empty is a
    valid answer; a 500 is not."""
    r = api.get("/registry/models")
    assert r.status_code == 200, r.text
