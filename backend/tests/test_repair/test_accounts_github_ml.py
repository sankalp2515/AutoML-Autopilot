"""BE-23 accounts and teams, BE-24 GitHub App webhook, BE-25 multiclass/regression ML checks."""
import hashlib
import hmac
import json
import subprocess
import sys
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from fastapi import FastAPI, Header
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import Base
from app.repair import accounts, github_app, models as m, routes
from app.repair.contracts import RepoContract
from app.repair.oracle import ml
from app.repair.service import RepairService

TABLES = [t for name, t in Base.metadata.tables.items() if name.startswith("repair_")]
SECRET = "s" * 40


@pytest_asyncio.fixture
async def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts.settings, "REPAIR_SESSION_SECRET", SECRET)
    monkeypatch.setattr(accounts.settings, "GITHUB_OAUTH_CLIENT_ID", "cid")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'r.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(lambda s: Base.metadata.create_all(s, tables=TABLES))
    yield RepairService(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


def _app(svc):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_service] = lambda: svc
    app.dependency_overrides[routes.autopilot_tenant] = lambda x_autopilot_session=Header("anon"): x_autopilot_session
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _github(login, uid):
    def handler(req: httpx.Request):
        if req.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_x"})
        return httpx.Response(200, json={"id": uid, "login": login, "name": login.title()})
    return httpx.MockTransport(handler)


async def _sign_in(svc, login, uid):
    state = accounts.oauth_start()["state"]
    prof = accounts.github_profile("code1", state, transport=_github(login, uid))
    return await accounts.sign_in(svc, prof)


# ── BE-23 ─────────────────────────────────────────────────────────────────────
async def test_github_sign_in_creates_personal_workspace_and_token(svc):
    with pytest.raises(accounts.AccountError):
        accounts.github_profile("code1", "forged-state-value", transport=_github("ann", 1))
    out = await _sign_in(svc, "ann", 1)
    assert out["account"]["login"] == "ann" and len(out["workspaces"]) == 1
    assert out["workspaces"][0]["role"] == "owner"
    assert accounts.read_session(out["token"])["sub"] == "gh:1"
    again = await _sign_in(svc, "ann", 1)                   # second sign-in reuses the workspace
    assert [w["id"] for w in again["workspaces"]] == [w["id"] for w in out["workspaces"]]
    assert "gho_x" not in json.dumps(again)                 # the GitHub token is never kept


async def test_roles_scope_tenancy_and_protect_last_owner(svc):
    ann, bob = await _sign_in(svc, "ann", 1), await _sign_in(svc, "bob", 2)
    ws = ann["personal_workspace"]
    h_ann = {"X-Autopilot-Token": ann["token"], "X-Workspace": ws}
    h_bob = {"X-Autopilot-Token": bob["token"], "X-Workspace": ws}
    async with _app(svc) as c:
        assert (await c.get("/api/v1/v5/tasks", headers=h_bob)).status_code == 404     # not a member
        r = await c.put(f"/api/v1/v5/workspaces/{ws}/members/bob", json={"role": "viewer"}, headers=h_ann)
        assert r.json() == {"login": "bob", "role": "viewer"}
        assert (await c.get("/api/v1/v5/tasks", headers=h_bob)).status_code == 200
        # a viewer reads but cannot stage repositories or start runs
        assert (await c.post("/api/v1/v5/repos", json={"sample": "churn-service"}, headers=h_bob)).status_code == 403
        assert (await c.post("/api/v1/v5/tasks", json={"repo_id": "a" * 12, "target": "fix it"},
                             headers=h_bob)).status_code == 403
        # a viewer cannot manage members; an admin cannot create owners
        assert (await c.put(f"/api/v1/v5/workspaces/{ws}/members/ann", json={"role": "viewer"},
                            headers=h_bob)).status_code == 403
        await c.put(f"/api/v1/v5/workspaces/{ws}/members/bob", json={"role": "admin"}, headers=h_ann)
        assert (await c.put(f"/api/v1/v5/workspaces/{ws}/members/bob", json={"role": "owner"},
                            headers=h_bob)).status_code == 403
        # the last owner cannot be demoted, even by themself
        r = await c.put(f"/api/v1/v5/workspaces/{ws}/members/ann", json={"role": "member"}, headers=h_ann)
        assert r.status_code == 422 and "owner" in r.json()["detail"]
        assert (await c.put(f"/api/v1/v5/workspaces/{ws}/members/zed", json={"role": "member"},
                            headers=h_ann)).status_code == 422                     # never signed in
        members = (await c.get(f"/api/v1/v5/workspaces/{ws}/members", headers=h_ann)).json()["members"]
        assert {(x["login"], x["role"]) for x in members} == {("ann", "owner"), ("bob", "admin")}
        me = (await c.get("/api/v1/v5/me", headers=h_bob)).json()
        assert me["role"] == "admin" and len(me["workspaces"]) == 2
        assert (await c.get("/api/v1/v5/me", headers={"X-Autopilot-Token": "junk"})).status_code == 401
        assert (await c.get("/api/v1/v5/me")).status_code == 401                         # anonymous
        assert (await c.get("/api/v1/v5/tasks")).status_code == 200                      # public demo still works


# ── BE-24 ─────────────────────────────────────────────────────────────────────
def _sig(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


FAILED_RUN = {"action": "completed", "installation": {"id": 77},
              "repository": {"full_name": "Acme/Churn", "clone_url": "https://github.com/acme/churn.git"},
              "workflow_run": {"conclusion": "failure", "head_sha": "a" * 40, "head_branch": "main",
                               "name": "tests", "html_url": "https://github.com/acme/churn/actions/runs/1"}}


def test_failed_run_parsing():
    assert github_app.failed_run("workflow_run", FAILED_RUN)["repo"] == "acme/churn"
    ok = {**FAILED_RUN, "workflow_run": {**FAILED_RUN["workflow_run"], "conclusion": "success"}}
    assert github_app.failed_run("workflow_run", ok) is None
    assert github_app.failed_run("push", FAILED_RUN) is None
    assert github_app.verify_signature(SECRET, b"x", _sig(b"x"))
    assert not github_app.verify_signature(SECRET, b"x", _sig(b"y"))


async def test_webhook_opens_a_task_only_for_linked_signed_failures(svc, monkeypatch):
    monkeypatch.setattr(github_app.settings, "GITHUB_WEBHOOK_SECRET", SECRET)
    ann = await _sign_in(svc, "ann", 1)
    h = {"X-Autopilot-Token": ann["token"]}
    seen = []

    async def fake_handle(svc_, delivery_id, run, link, create):
        seen.append((delivery_id, run["sha"], link.workspace_id, link.installation_id))
    monkeypatch.setattr(github_app, "handle_failure", fake_handle)
    body = json.dumps(FAILED_RUN).encode()

    def hook(data=body, sig=None, delivery="d1", event="workflow_run"):
        return c.post("/api/v1/v5/github/webhook", content=data,
                      headers={"X-GitHub-Event": event, "X-GitHub-Delivery": delivery,
                               "X-Hub-Signature-256": sig or _sig(data)})
    async with _app(svc) as c:
        assert (await hook(sig="sha256=00")).status_code == 401
        assert (await hook()).json()["reason"] == "repository is not linked to a workspace"
        assert (await c.post("/api/v1/v5/github/links", json={"repo": "acme/churn"}, headers=h)).status_code == 201
        assert (await hook()).json()["accepted"] is True
        assert (await hook()).json()["reason"] == "duplicate delivery"        # GitHub redelivery is a no-op
        assert (await hook(event="ping", data=b"{}")).json() == {"ok": True}
        links = (await c.get("/api/v1/v5/github/links", headers=h)).json()["links"]
    assert seen == [("d1", "a" * 40, ann["personal_workspace"], 77)]
    assert links[0]["installed"] and links[0]["recent"][0]["outcome"] == "queued"


async def test_webhook_background_skips_repos_without_a_contract(svc, monkeypatch):
    monkeypatch.setattr(github_app.settings, "REPAIR_LIVE_ENABLED", True)
    ann = await _sign_in(svc, "ann", 1)
    await github_app.save_link(svc, ann["personal_workspace"], "gh:1", "acme/churn", True)
    run = github_app.failed_run("workflow_run", FAILED_RUN)
    link = await github_app.link_for(svc, run)
    await github_app.record_delivery(svc, "d2", "workflow_run", run["repo"], "queued")
    from app.repair import snapshots
    monkeypatch.setattr(snapshots, "stage_url", lambda *a: {"id": "x", "contract_source": "proposed",
                                                            "contract_errors": ["no autopilot.yaml"]})

    async def never(*a):
        raise AssertionError("no task without the repository's own contract")
    await github_app.handle_failure(svc, "d2", run, link, never)
    async with svc.sessions() as db:
        assert "no valid autopilot.yaml" in (await db.get(m.WebhookDelivery, "d2")).outcome


def test_installation_token_uses_a_signed_app_jwt(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    monkeypatch.setattr(github_app.settings, "GITHUB_APP_ID", "123")
    monkeypatch.setattr(github_app.settings, "GITHUB_APP_PRIVATE_KEY", pem.replace("\n", "\\n"))
    import jwt

    def handler(req: httpx.Request):
        claims = jwt.decode(req.headers["Authorization"][7:], key.public_key(), algorithms=["RS256"])
        assert claims["iss"] == "123" and json.loads(req.content)["repositories"] == ["churn"]
        return httpx.Response(201, json={"token": "ghs_t"})
    assert github_app.installation_token(77, "acme/churn", httpx.MockTransport(handler)) == "ghs_t"


# ── BE-25 ─────────────────────────────────────────────────────────────────────
BASE = {"version": 1, "name": "r", "python": "3.12", "install": "pip install -r requirements.txt",
        "commands": {"test": "pytest -q", "train": "python -m p.train", "predict": "python -m p.predict"},
        "policy": {"allowed_edit_roots": ["p/"]}}


def _contract(problem, metric):
    return {**BASE, "data": {"train": "d/t.csv", "holdout_features": "d/h.csv", "id_column": "id", "target": "y",
                             "problem": problem},
            "task": {"type": "preserve", "metric": metric, "ml_invariants": ["holdout_non_inferior"]}}


def test_contract_metric_must_fit_the_problem():
    assert RepoContract.model_validate(_contract("regression", "rmse")).data.problem == "regression"
    assert RepoContract.model_validate(_contract("multiclass", "macro_f1"))
    with pytest.raises(ValueError, match="does not fit a regression"):
        RepoContract.model_validate(_contract("regression", "roc_auc"))


def test_predictions_for_multiclass_and_regression():
    ids = pd.Series(["1", "2", "3"])
    mc = ml.read_predictions(b"id,prediction\n1,cat\n2,dog\n3,cat\n", "id", ids, "multiclass")
    assert list(mc["decision"]) == ["cat", "dog", "cat"]
    rg = ml.read_predictions(b"id,prediction\n1,1.5\n2,2\n3,-4\n", "id", ids, "regression")
    assert list(rg["score"]) == [1.5, 2.0, -4.0]
    with pytest.raises(ml.PredictionError):
        ml.read_predictions(b"id,prediction\n1,1.5\n2,nan\n3,1\n", "id", ids, "regression")
    with pytest.raises(ml.PredictionError):
        ml.read_predictions(b"id,score\n1,0.5\n2,0.5\n3,0.5\n", "id", ids, "multiclass")


def test_error_metrics_are_oriented_as_relative_improvement():
    rng = np.random.default_rng(0)
    y = rng.normal(10, 2, 400)
    good, bad = y + rng.normal(0, 0.5, 400), y + rng.normal(0, 2.0, 400)
    d = ml.paired_delta("rmse", y, bad, bad, good, good)
    assert d["delta"] > 0.5 and d["ci_low"] > 0 and d["relative"]         # better = positive
    assert ml.paired_delta("rmse", y, good, good, bad, bad)["ci_high"] < 0
    lab = np.array(["a", "b", "c"] * 50)
    assert ml._metric("macro_f1", lab, lab, lab) == 1.0
    assert ml.improvement("mae", 2.0, 1.0) == 0.5 and ml.improvement("r2", 0.5, 0.6) == pytest.approx(0.1)


def test_regression_segment_guardrail_flags_a_group_that_got_worse():
    from types import SimpleNamespace
    n = 120
    ids = [str(i) for i in range(n)]
    y = np.linspace(0, 10, n)
    seg = np.array(["north", "south"] * (n // 2))
    base = pd.DataFrame({"score": y + 0.1, "decision": y + 0.1}, index=ids)
    worse = np.where(seg == "south", y + 3.0, y + 0.1)
    cand = pd.DataFrame({"score": worse, "decision": worse}, index=ids)
    labels = pd.DataFrame({"y": y, "region": seg}, index=ids)
    data = SimpleNamespace(segment_column="region", id_column="id", problem="regression", segment_max_drop=0.05)
    metrics = {}
    chk = ml._segments(data, labels, pd.DataFrame({"id": ids}), cand, base, y, metrics)
    assert chk.status.value == "FAIL" and metrics["segments"]["metric"] == "mae"
    assert metrics["segments"]["by_group"]["north"]["delta"] == 0


def test_parity_probe_supports_labels_and_custom_loaders(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").write_text("")
    (tmp_path / "pkg/serve.py").write_text(
        "import json\n"
        "def load(path):\n    return json.load(open(path))\n"
        "def predict(row, model):\n    return model['labels'][int(row['x']) % 2]\n")
    (tmp_path / "model.json").write_text('{"labels": ["cat", "dog"]}')
    pd.DataFrame({"id": [1, 2, 3, 4], "x": [0, 1, 2, 3]}).to_csv(tmp_path / "h.csv", index=False)
    probe = Path(ml.HARNESS)
    subprocess.run([sys.executable, str(probe), "--entrypoint", "pkg.serve:predict", "--model", "model.json",
                    "--loader", "pkg.serve:load", "--input", "h.csv", "--id-column", "id",
                    "--out", "o.json", "--kind", "label"], cwd=tmp_path, check=True)
    rep = json.loads((tmp_path / "o.json").read_text())
    # the probe only records; it never sees batch predictions or decides anything (BUG-05)
    assert rep == {"online": [["1", "cat"], ["2", "dog"], ["3", "cat"], ["4", "dog"]], "errors": []}


def test_parity_verdict_is_computed_by_the_oracle_not_the_probe():
    from app.repair.sandbox.base import RunResult
    fake = {"scores": ["cat", "dog", "cat", "dog"], "errors": [], "max_abs_diff": 0.0}
    class Runner:
        def fork(self, env): return "online"
        def write_files(self, env, files):
            assert files["_ap/predictions.csv"] is None
        def run(self, *a, **k): return RunResult(0, False, 1, "", "", False)
        def read_file(self, env, path):
            if env == "e": return b"id,prediction\n1,cat\n2,dog\n3,cat\n4,cat\n"
            return json.dumps(fake).encode()
        def discard(self, env): assert env == "online"
    contract = RepoContract.model_validate({**_contract("multiclass", "accuracy"), "entrypoints": {"serve": "p.s:f"}})
    features = pd.DataFrame({"id": [1,2,3,4], "x": [0,1,2,3]})
    chk = ml._parity(Runner(), "e", contract, 4, 1e-6, features)
    assert chk.status.value == "FAIL" and chk.evidence["mismatched_rows"] == 1


# ── BE-15 ranked probes, BE-16 drift profile ──────────────────────────────────
def _fake_agent(tmp_path, fail_to_pass=()):
    from types import SimpleNamespace
    from app.repair.agent.loop import RepairReport
    events = []
    data = SimpleNamespace(train="d/train.csv", holdout_features="d/hold.csv", id_column="id", target="y")
    return SimpleNamespace(base_dir=str(tmp_path), mode="full", events=events,
                           contract=SimpleNamespace(data=data),
                           ecc=SimpleNamespace(success=SimpleNamespace(fail_to_pass=list(fail_to_pass))),
                           report=RepairReport(task_id="t", mode="full", contract_digest="x", runner="process"),
                           _event=lambda t, a, **d: events.append((t, d)))


def test_probes_rank_confirmed_hypotheses_first(tmp_path):
    from app.repair.agent.loop import Hypothesis, RepairAgent
    (tmp_path / "serve.py").write_text("COLUMNS = ['a', 'b']  # hand-written copy\n")
    hyps = [Hypothesis(id="H1", cause="scaler", probe={"path": "serve.py", "pattern": "StandardScaler"}),
            Hypothesis(id="H2", cause="no probe"),
            Hypothesis(id="H3", cause="column copy", probe={"path": "serve.py", "pattern": "COLUMNS = ["}),
            Hypothesis(id="H4", cause="escape", probe={"path": "../x.py", "pattern": "abc"}),
            Hypothesis(id="H5", cause="bad probe", probe={"path": "serve.py"})]      # malformed → dropped
    agent = _fake_agent(tmp_path)
    ranked = RepairAgent._probe(agent, hyps)
    assert [h.id for h in ranked] == ["H3", "H2", "H4", "H5", "H1"]
    assert {h.id: h.status for h in ranked} == {"H1": "refuted", "H2": "untested", "H3": "confirmed",
                                                "H4": "unrunnable", "H5": "untested"}
    assert agent.events[-1][0] == "hypothesis.revised"


def test_drift_profile_runs_only_without_a_failing_test(tmp_path):
    from app.repair.agent.loop import RepairAgent
    (tmp_path / "d").mkdir()
    rng = np.random.default_rng(0)
    pd.DataFrame({"id": range(500), "y": 0, "tenure": rng.normal(10, 1, 500),
                  "plan": ["a"] * 500}).to_csv(tmp_path / "d/train.csv", index=False)
    pd.DataFrame({"id": range(300), "tenure": rng.normal(16, 1, 300),
                  "plan": ["a"] * 300}).to_csv(tmp_path / "d/hold.csv", index=False)
    agent = _fake_agent(tmp_path)
    RepairAgent._data_profile(agent)
    assert [f["column"] for f in agent.report.data_profile["flagged"]] == ["tenure"]
    other = _fake_agent(tmp_path, fail_to_pass=["tests/test_x.py::test_y"])
    RepairAgent._data_profile(other)
    assert other.report.data_profile == {}


# ── BE-19 ─────────────────────────────────────────────────────────────────────
def test_alembic_upgrades_v4_schema_to_v5_and_back(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect
    from app.config import settings
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL_SYNC", url)
    cfg = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).parents[2] / "alembic"))
    import app.models.run  # noqa: F401
    v4 = [t for n, t in Base.metadata.tables.items() if not n.startswith("repair_")]
    Base.metadata.create_all(create_engine(url), tables=v4)      # a deployed v4 database...
    command.stamp(cfg, "0002_tenant_id")                         # ...at the last v4 revision
    names = lambda: set(inspect(create_engine(url)).get_table_names())   # noqa: E731
    assert "repair_tasks" not in names()
    command.upgrade(cfg, "head")
    assert {"repair_tasks", "repair_accounts", "repair_memberships", "repair_github_links"} <= names()
    cols = {c["name"] for c in inspect(create_engine(url)).get_columns("repair_tasks")}
    assert {"spec", "report"} <= cols
    command.downgrade(cfg, "0002_tenant_id")
    assert not any(n.startswith("repair_") for n in names())


# ── BE-22 metrics ─────────────────────────────────────────────────────────────
async def test_metrics_report_queue_runtime_tokens_and_cost(svc):
    from types import SimpleNamespace
    from app.repair import ops
    now = 2_000_000.0
    rows = [("CREATED", now - 30, None), ("CREATED", now - 90, None),
            ("REVIEW_READY", now - 900, {"decision": "VERIFIED", "seconds": 600,
                                         "usage": {"super": {"prompt_tokens": 100, "completion_tokens": 20},
                                                   "total_est_cost_usd": 0.14}}),
            ("REJECTED", now - 800, {"decision": "REJECTED", "seconds": 400, "usage": {"total_est_cost_usd": 0.1}})]

    async def recent(ts):
        return rows
    out = await ops.metrics(SimpleNamespace(recent=recent), now=now)
    assert out["queue_depth"] == 2 and out["oldest_queued_s"] == 90 and out["finished"] == 2
    assert out["decisions"] == {"VERIFIED": 1, "REJECTED": 1} and out["model_tokens"] == 120
    assert out["cost_usd"] == 0.24 and out["run_seconds_p95"] == 600
    assert 'autopilot_repair_decisions{decision="VERIFIED"} 1' in ops.prometheus(out)
    async with _app(svc) as c:
        assert (await c.get("/api/v1/v5/metrics")).json()["tasks"] == 0


@pytest.mark.parametrize("problem, values", [("binary", [0.2, 0.8]), ("multiclass", ["cat", "dog"]), ("regression", [-12.5, 20.0])])
def test_fresh_online_adapter_supports_custom_loaders_and_problem_outputs(tmp_path, problem, values):
    (tmp_path / "adapter.py").write_text("import json\ndef load(path):\n    return json.load(open(path))\ndef score(row, model):\n    return model[int(row['x'])]\n")
    (tmp_path / "model.json").write_text(json.dumps(values))
    pd.DataFrame({"id": [1,2], "x": [0,1]}).to_csv(tmp_path / "rows.csv",index=False)
    subprocess.run([sys.executable,str(Path(ml.HARNESS).with_name("online_scores.py")),"--entrypoint","adapter:score","--loader","adapter:load","--model","model.json","--input","rows.csv","--out","result.json","--rows","2","--problem",problem],cwd=tmp_path,check=True,timeout=60)
    result=json.loads((tmp_path / "result.json").read_text())
    assert result == {"scores": values, "errors": []}
