"""LOCAL QA ONLY: real API/SQLite/worker with seeded identities and scripted inference.
Run directly, never mount in a production app. No provider or GitHub network calls.
Writes ephemeral test credentials into --state-dir for the browser test process.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from pydantic_settings.sources import DotEnvSettingsSource
DotEnvSettingsSource.__call__ = lambda self: {}
from app.config import settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--port", type=int, default=8112)
    args = parser.parse_args()
    state = Path(args.state_dir); state.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix="repair-ui-", dir=state))
    settings.DATABASE_URL = "sqlite+aiosqlite:///" + (run_dir / "qa.db").as_posix()
    settings.DATA_DIR = str(run_dir / "data")
    settings.REPAIR_SESSION_SECRET = secrets.token_hex(32)
    settings.NEBIUS_API_KEY = settings.NVIDIA_API_KEY = settings.TAVILY_API_KEY = ""
    settings.GITHUB_OAUTH_CLIENT_ID = settings.GITHUB_OAUTH_CLIENT_SECRET = ""
    settings.REPAIR_LIVE_ENABLED = True
    settings.REPAIR_INVITE_CODES = ""
    settings.REPAIR_DAILY_TASKS_PER_TENANT = 100
    settings.REPAIR_DAILY_USD_CAP = 100
    settings.REPAIR_RUNNER = "process"
    settings.REPAIR_SANDBOX_URL = settings.REPAIR_VERIFIER_URL = ""
    settings.REPAIR_FIXTURES_DIR = str(ROOT / "fixtures/repos")
    settings.REPAIR_REPLAYS_DIR = str(ROOT / "frontend/data/repair")
    from fastapi import FastAPI
    from app.database import init_db
    from app.repair import accounts, engine, routes, service
    from app.repair.agent.llm import ChatClient
    from app.repair.agent.oracles import LocalOracles
    from app.repair.sandbox.process import ProcessRunner
    spec = importlib.util.spec_from_file_location("scripted_repair", ROOT / "backend/tests/test_repair/test_agent.py")
    fixtures = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixtures)
    fixtures.HYPS["hypotheses"][0]["probe"] = {"path":"churn/serve.py","pattern":"SERVING_ORDER = [","expect":"present"}
    fixtures.HYPS["hypotheses"][1]["probe"] = {"path":"churn/serve.py","pattern":"SERVING_ORDER = [","expect":"absent"}
    runner = ProcessRunner(str(run_dir / "environments"), allow_unisolated=True)
    engine.make_runner = lambda: runner
    engine.make_oracles = lambda r, ref: LocalOracles(r, ref)
    engine.client_from_settings = lambda budget: ChatClient(provider="scripted-ui-qa",base_url="http://model.invalid/v1",api_key="test-only",models={"ultra":"scripted-planner","super":"scripted-coder","nano":"scripted-fast"},budget=budget,transport=fixtures.scripted_transport())
    async def worker():
        while True:
            if not await service.default_service.work_once(owner="ui-qa-scripted"):
                await asyncio.sleep(.3)
    @asynccontextmanager
    async def lifespan(app):
        await init_db()
        ann = await accounts.sign_in(service.default_service, {"id":901,"login":"qa-owner","name":"QA Owner"})
        bob = await accounts.sign_in(service.default_service, {"id":902,"login":"qa-viewer","name":"QA Viewer"})
        team = await accounts.create_workspace(service.default_service,ann["account"]["id"],"QA Team")
        await accounts.set_member(service.default_service,team["id"],"owner","qa-viewer","viewer")
        (state / "identity.json").write_text(json.dumps({"owner":ann,"viewer":bob,"team":team}),encoding="utf-8")
        task = asyncio.create_task(worker())
        try: yield
        finally:
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
    app = FastAPI(lifespan=lifespan); app.include_router(routes.router)
    import uvicorn
    uvicorn.run(app,host="127.0.0.1",port=args.port,log_level="warning")

if __name__ == "__main__": main()
