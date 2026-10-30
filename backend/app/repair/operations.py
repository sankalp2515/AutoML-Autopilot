"""Local operator commands inside the service image. No model or GitHub calls."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import time
from sqlalchemy import select,func,text
from app.config import settings


def heartbeat_path():return Path(tempfile.gettempdir())/"repair-worker-alive"


def migrate():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine,inspect as sql_inspect
    from app.database import Base
    import app.models.run,app.autopilot.models,app.repair.models
    settings.DATABASE_URL_SYNC=settings.DATABASE_URL.replace("postgresql+asyncpg:","postgresql+psycopg2:").replace("sqlite+aiosqlite:","sqlite:")
    engine=create_engine(settings.DATABASE_URL_SYNC)
    config=Config(str(Path(__file__).resolve().parents[2]/"alembic.ini"))
    config.set_main_option("script_location",str(Path(__file__).resolve().parents[2]/"alembic"))
    inspector=sql_inspect(engine);tables=set(inspector.get_table_names())
    if not tables:
        Base.metadata.create_all(engine)
        command.stamp(config,"head")
    else:
        if "alembic_version" not in tables:
            if "runs" not in tables:raise ValueError("unrecognized application schema; manual migration review required")
            version="0002_tenant_id" if "tenant_id" in {c["name"] for c in inspector.get_columns("runs")} else "0001_baseline"
            command.stamp(config,version)
        command.upgrade(config,"head")
    engine.dispose()
    return {"migrated":True}


def ledger_inspection(seed=False):
    from app.repair.oracle.ledger import DatabaseLedger,BoundLedger
    if not settings.REPAIR_LEDGER_DATABASE_URL:raise ValueError("run this inside the verifier, not the API")
    ledger=DatabaseLedger(settings.REPAIR_LEDGER_DATABASE_URL);ledger.initialize()
    if seed:
        ledger.admit("backup-drill","diagnostic-only-admission")
        BoundLedger(ledger,"diagnostic-only-patch").consume("backup-drill","candidate")
    snapshot=ledger.snapshot()
    return {"sha256":hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest(),
            "admissions":len(snapshot["admissions"]),"openings":len(snapshot["openings"]),
            "database_superuser":snapshot["database_superuser"]}


async def inspect():
    from app.repair.service import default_service as svc
    from app.repair import models as m
    from app.autopilot.models import AutopilotInvestigation
    from app.autopilot.schemas import TERMINAL
    async with svc.sessions() as db:
        receipts=(await db.execute(select(m.Receipt.id,m.Receipt.payload).order_by(m.Receipt.id))).all()
        tasks=(await db.execute(select(func.count()).select_from(m.Task))).scalar()
        marker=await db.get(m.AdmissionLock,"backup-drill")
        superuser=(await db.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user"))).scalar() if db.bind.dialect.name=="postgresql" else None
        v4_active=(await db.execute(select(func.count()).select_from(AutopilotInvestigation).where(
            AutopilotInvestigation.status.notin_([s.value for s in TERMINAL])))).scalar()
    replay_dir=Path(settings.DATA_DIR)/"repair/replays"
    embedded={}
    for path in sorted(replay_dir.glob("*.json")):
        report=json.loads(path.read_text())
        for index,row in enumerate(report.get("candidates",[])):
            for kind in ("code_receipt","screen_receipt"):
                if row.get(kind):embedded[f"{path.name}/{index}/{kind}"]=hashlib.sha256(json.dumps(row[kind],sort_keys=True).encode()).hexdigest()
        if report.get("final_ml_receipt"):
            embedded[path.name+"/final_ml_receipt"]=hashlib.sha256(json.dumps(report["final_ml_receipt"],sort_keys=True).encode()).hexdigest()
    return {"tasks":tasks,"receipt_hashes":{k:hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest() for k,v in receipts},
            "replay_receipt_hashes":embedded,
            "replay_hashes":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(replay_dir.glob("*.json"))},
            "drill_marker":marker.version if marker else None,"active_tasks":len(await svc.active_specs())+v4_active,
            "queue_db_superuser":superuser}


async def warm():
    from app.repair import snapshots
    from app.repair.sandbox.factory import make_runner
    runner=make_runner();rows=[]
    for sample in snapshots.samples():
        started=time.monotonic()
        staged=await asyncio.to_thread(snapshots.stage_sample,"operator-warm",sample["name"])
        frozen=await asyncio.to_thread(snapshots.freeze,staged["id"],"operator-warm")
        rc=frozen["contract"]
        env=await asyncio.to_thread(runner.prepare,frozen["snapshot_dir"],python=rc.python,install=rc.install,timeout_s=900)
        try:
            image=runner.environment_image(env)
        finally:await asyncio.to_thread(runner.discard,env)
        rows.append({"sample":sample["name"],"image":image,"seconds":round(time.monotonic()-started,2)})
    return rows


async def seed_drill():
    """Seed an actual historical replay and a DB sentinel; this executes no repair."""
    from app.repair import ops,models as m
    from app.repair.service import default_service as svc
    source=Path(settings.REPAIR_REPLAYS_DIR)/"nvidia-pilot/churn-service-7-D/report.json"
    report=json.loads(source.read_text())
    replay=ops.publish_replay(report["task_id"],report,{"target":"Imported historical NVIDIA evidence for restore drill","sample":"churn-service"})
    async with svc.sessions() as db,db.begin():
        marker=await db.get(m.AdmissionLock,"backup-drill")
        if marker is None:db.add(m.AdmissionLock(id="backup-drill",version=429))
        else:marker.version=429
    return {"replay_id":replay,"new_model_calls":0,**await inspect()}


async def pause_admission(paused):
    from app.repair import admission,models as m
    from app.repair.service import default_service as svc
    async with svc.sessions() as db,db.begin():
        await admission.lock(db)
        row=await db.get(m.AdmissionLock,"maintenance")
        if row is None:db.add(m.AdmissionLock(id="maintenance",version=int(paused)))
        else:row.version=int(paused)
    return {"admission_paused":paused}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["worker-health","inspect","warm","seed-drill","migrate","pause-admission","resume-admission","inspect-ledger","seed-ledger-drill"])
    args=parser.parse_args()
    if args.command=="worker-health":
        path=heartbeat_path();ok=path.is_file() and time.time()-path.stat().st_mtime<60
        print(json.dumps({"healthy":ok}));raise SystemExit(0 if ok else 1)
    if args.command=="migrate":print(json.dumps(migrate()));return
    if args.command in {"inspect-ledger","seed-ledger-drill"}:
        print(json.dumps(ledger_inspection(args.command=="seed-ledger-drill")));return
    if args.command.endswith("-admission"):
        print(json.dumps(asyncio.run(pause_admission(args.command=="pause-admission"))));return
    print(json.dumps(asyncio.run({"inspect":inspect,"warm":warm,"seed-drill":seed_drill}[args.command]()),indent=2))


if __name__=="__main__":main()
