"""v5 backend: intake, packs, security, mirror, delivery, replays, admission, research,
and one durable end-to-end run (service + engine + agent + in-process grading)."""
import io
import json
import sys
import zipfile
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.database import Base
from app.repair import contracts as c
from app.repair import delivery, ops, security, snapshots
from app.repair.agent import research
from app.repair.sandbox.mirror import MirroredEnv
from app.repair.sandbox.process import ProcessRunner
from app.repair.service import RepairService

ROOT = Path(__file__).resolve().parents[3]
FIX = ROOT / "fixtures" / "repos"
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    for k, v in {"DATA_DIR": str(tmp_path / "data"), "REPAIR_RUNNER": "process", "REPAIR_ENV_ROOT": str(tmp_path / "envs"),
                 "REPAIR_SANDBOX_URL": "", "REPAIR_VERIFIER_URL": "", "REPAIR_VERIFIER_STATE_DIR": "",
                 "REPAIR_PACKS_DIR": "", "REPAIR_FIXTURES_DIR": str(FIX), "REPAIR_LIVE_ENABLED": True,
                 "REPAIR_INVITE_CODES": "", "TAVILY_API_KEY": ""}.items():
        monkeypatch.setattr(settings, k, v)


@pytest_asyncio.fixture
async def svc(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'r.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield RepairService(async_sessionmaker(engine, expire_on_commit=False))
    await engine.dispose()


def test_redaction_and_url_guard():
    assert "[REDACTED]" in security.redact("key nvapi-" + "a" * 30) and "abc" in security.redact("abc")
    assert "abcdefghijklmnop1234" not in security.redact("api_key = 'abcdefghijklmnop1234'")
    for bad in ("http://github.com/a/b", "https://user:pw@github.com/a/b", "https://evil.example/a/b",
                "https://github.com:8443/a/b"):
        with pytest.raises(security.UnsafeSource):
            security.check_repo_url(bad)


def test_unpinned_requirements(tmp_path):
    (tmp_path / "requirements.txt").write_text("pandas==2.3.3\nnumpy>=1\ngit+https://x/y\n# c\n")
    assert security.unpinned_requirements(tmp_path) == ["numpy>=1", "git+https://x/y"]


def test_mirror_syncs_only_changes_into_the_sandbox(tmp_path):
    repo = tmp_path / "r"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("x = 1\n")
    runner = ProcessRunner(str(tmp_path / "e"), allow_unisolated=True)
    env = runner.prepare(str(repo), python="3.12", install="pip install -r requirements.txt", timeout_s=30)
    m = MirroredEnv(runner, env, str(repo))
    assert m.sync() == 0
    (m.dir / "pkg" / "a.py").write_bytes(b"x = 2\n")
    (m.dir / "pkg" / "b.py").write_text("y = 1\n")
    assert m.sync() == 2 and runner.read_file(env, "pkg/a.py") == b"x = 2\n"
    (m.dir / "pkg" / "b.py").unlink()
    m.sync()
    with pytest.raises(FileNotFoundError):
        runner.read_file(env, "pkg/b.py")
    m.close()


def test_sample_staging_contract_and_freeze():
    assert any(s["name"] == "churn-service" for s in snapshots.samples())
    info = snapshots.stage_sample("t1", "churn-service")
    assert info["contract_source"] == "repository" and not info["contract_errors"]
    with pytest.raises(snapshots.StageError):
        snapshots.describe(info["id"], "someone-else")
    frozen = snapshots.freeze(info["id"], "t1")
    assert frozen["contract"].task.fail_to_pass and Path(frozen["snapshot_dir"], "autopilot.yaml").is_file()


def test_zip_upload_gets_a_contract_proposal_and_validation(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("proj/mlpkg/__init__.py", "")
        z.writestr("proj/mlpkg/train.py", "")
        z.writestr("proj/mlpkg/predict.py", "")
        z.writestr("proj/mlpkg/serve.py", "def score_request(payload, model):\n    return 0.5\n")
        z.writestr("proj/tests/test_x.py", "def test_x():\n    assert True\n")
        z.writestr("proj/requirements.txt", "pandas==2.3.3\n")
        z.writestr("proj/../evil.txt", "x")
    info = snapshots.stage_zip("t1", buf.getvalue(), "proj.zip")
    assert info["contract_source"] == "proposed" and "evil.txt" not in info["files"]
    assert "mlpkg.serve:score_request" in info["contract_yaml"] and info["needs_input"]
    with pytest.raises(snapshots.StageError):
        snapshots.save_contract(info["id"], "t1", info["contract_yaml"])        # placeholders still unresolved
    ok = "version: 1\nname: proj\ncommands: {test: 'pytest -q tests'}\ntask: {type: restore}\n" \
         "policy: {allowed_edit_roots: ['mlpkg/', 'tests/']}\n"
    out = snapshots.save_contract(info["id"], "t1", ok, ["tests/test_x.py::test_x"])
    assert out["contract_source"] == "edited" and "tests/test_x.py::test_x" in out["contract_yaml"]


def test_evaluation_pack_is_validated_and_owned():
    csv = b"id,label,region\n1,1,a\n2,0,b\n3,1,a\n"
    p = snapshots.create_pack("t1", csv, id_column="id", target="label", positive_label="1", segment_column="region")
    assert p["rows"] == 3 and snapshots.pack_owner(p["id"]) == "t1"
    with pytest.raises(snapshots.StageError):
        snapshots.create_pack("t1", csv, id_column="id", target="missing", positive_label="1", segment_column=None)
    with pytest.raises(snapshots.StageError):
        snapshots.delete_pack(p["id"], "other")


def test_replays_are_listed_from_bundled_runs(monkeypatch):
    monkeypatch.setattr(settings, "REPAIR_REPLAYS_DIR", str(ROOT / "docs" / "benchmarks" / "repair"))
    rs = ops.list_replays()
    assert {"churn_live_prompt_v1", "churn_live_prompt_v2"} <= {r["id"] for r in rs}
    assert ops.get_replay("churn_live_prompt_v2")["decision"] == "VERIFIED"
    with pytest.raises(FileNotFoundError):
        ops.get_replay("../../etc/passwd")


async def test_admission_caps(svc, monkeypatch):
    monkeypatch.setattr(settings, "REPAIR_LIVE_ENABLED", False)
    with pytest.raises(ops.Denied):
        await ops.admit_live_run(svc, "t1", None)
    monkeypatch.setattr(settings, "REPAIR_LIVE_ENABLED", True)
    monkeypatch.setattr(settings, "REPAIR_INVITE_CODES", "alpha,beta")
    with pytest.raises(ops.Denied):
        await ops.admit_live_run(svc, "t1", "wrong")
    assert (await ops.admit_live_run(svc, "t1", "beta"))["cap_usd"] == settings.REPAIR_DAILY_USD_CAP
    monkeypatch.setattr(settings, "REPAIR_DAILY_USD_CAP", 0.1)
    with pytest.raises(ops.Denied):
        await ops.admit_live_run(svc, "t1", "beta")


def test_research_cites_official_docs_and_quarantines_injection():
    from app.autopilot import research as r

    def handler(req):
        body = json.loads(req.content)
        if req.url.path.endswith("/search"):
            assert body["include_domains"] == ["scikit-learn.org"] and "OneHotEncoder" in body["query"]
            return httpx.Response(200, json={"results": [{"url": "https://scikit-learn.org/stable/whats_new/v1.2.html",
                                                          "title": "Release 1.2", "content": "OneHotEncoder sparse renamed sparse_output"}]})
        return httpx.Response(200, json={"results": [{"url": body["urls"][0],
                                                      "raw_content": "OneHotEncoder: sparse was renamed to sparse_output in 1.2."}]})
    client = r.TavilyClient("tvly-test", transport=httpx.MockTransport(handler))
    tb = "E   TypeError: OneHotEncoder.__init__() got an unexpected keyword argument 'sparse'\n  sklearn/preprocessing"
    out = research.research(tb, client=client)
    assert out["state"] == "AVAILABLE" and "sparse_output" in out["evidence"][0]["excerpt"]
    assert "untrusted data" in research.as_brief(out)
    assert research.research("AssertionError in my own code", client=client)["state"] == "NOT_NEEDED"


def test_draft_pr_uses_git_data_api_and_never_merges(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("x = 1\n")
    diff = "--- a/pkg/a.py\n+++ b/pkg/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path))
        p = req.url.path
        if p.endswith("/git/commits/" + "a" * 40):
            return httpx.Response(200, json={"tree": {"sha": "t0"}})
        if p.endswith("/pulls"):
            assert json.loads(req.content)["draft"] is True
            return httpx.Response(201, json={"html_url": "https://github.com/o/r/pull/1", "number": 1})
        return httpx.Response(201, json={"sha": "s1"})
    out = delivery.create_draft_pr(token="ghp_x" * 3, repo="o/r", base_sha="a" * 40, base_branch="main",
                                   snapshot_dir=str(tmp_path), diff=diff, title="t", body="b",
                                   transport=httpx.MockTransport(handler))
    assert out["number"] == 1 and not any("merge" in p for _, p in seen)


@pytest.mark.slow
async def test_durable_run_end_to_end(svc, tmp_path, monkeypatch):
    """Sample repo → frozen task → worker engine with a scripted model → REVIEW_READY with
    persisted events, candidates and receipts."""
    from test_agent import scripted_transport
    from app.repair import engine
    from app.repair.agent.llm import ChatClient
    monkeypatch.setattr(engine, "client_from_settings", lambda budget: ChatClient(
        provider="scripted", base_url="http://model.test/v1", api_key="x", budget=budget,
        models={"ultra": "nemotron-3-ultra", "super": "nemotron-3-super", "nano": "n"}, transport=scripted_transport()))
    info = snapshots.stage_sample("t1", "churn-service")
    fr = snapshots.freeze(info["id"], "t1")
    task = await svc.create("t1", contract=fr["contract"], base_commit_sha=fr["sha"], repo_snapshot_digest=fr["digest"],
                            target="Online scoring raises after the feature refactor",
                            spec={"snapshot_dir": fr["snapshot_dir"], "sealed_ref": "fixture:churn-service",
                                  "mode": "full", "sample": "churn-service", "source": "sample"})
    assert await svc.work_once(owner="w1")
    t = await svc.get(task["id"], "t1")
    assert t["status"] == "REVIEW_READY" and t["decision"] == "VERIFIED", t
    events = await svc.events(task["id"], "t1", limit=500)
    types = [e["type"] for e in events]
    telemetry=[e["data"] for e in events if e["type"]=="diagnostic.executed" and e["data"].get("kind")=="telemetry"]
    progress=[e for e in telemetry if e["telemetry_type"]=="candidate.progress"]
    assert progress and any("churn/serve.py" in e["diff"] for e in progress)
    assert all(e["telemetry_version"]==1 and e["step"]["n"]>=1 for e in progress)
    assert any(e["telemetry_type"]=="usage.updated" and e["usage"] for e in telemetry)
    for needed in ("repo.snapshotted", "hypothesis.proposed", "patch.applied", "tool.denied", "ml_oracle.rejected",
                   "candidate.frozen", "candidate.verified"):
        assert needed in types, needed
    cands = await svc.candidates(task["id"], "t1")
    assert len(cands) == 2 and any(x["eligible"] for x in cands) and all("steps" in x for x in cands)
    rec = await svc.receipts(t["finalist"], "t1", task_id=task["id"])
    assert set(rec) == {"code", "ml", "combined"} and rec["ml"]["holdout_opened"]
