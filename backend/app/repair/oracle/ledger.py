"""Verifier-only transactional admissions and one immutable finalist per task.

Uses a separate database credential/store. The async driver is bridged for the
synchronous grader, with a fresh NullPool engine per operation (no loop sharing).
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool


class VerifierBase(DeclarativeBase):
    pass


class Admission(VerifierBase):
    __tablename__="verifier_admissions"
    task_id: Mapped[str]=mapped_column(String(64),primary_key=True)
    digest: Mapped[str]=mapped_column(String(80))


class Opening(VerifierBase):
    __tablename__="verifier_holdout_openings"
    task_id: Mapped[str]=mapped_column(String(64),primary_key=True)
    candidate: Mapped[str]=mapped_column(String(200))


def sync(coroutine):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run,coroutine).result()


class DatabaseLedger:
    def __init__(self,url):
        self.url=url

    async def _operation(self,operation,*args):
        engine=create_async_engine(self.url,poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                dialect=conn.dialect.name
                if dialect=="postgresql":
                    from sqlalchemy.dialects.postgresql import insert
                elif dialect=="sqlite":
                    from sqlalchemy.dialects.sqlite import insert
                else:
                    raise PermissionError("unsupported verifier database")
                if operation=="ping":
                    await conn.exec_driver_sql("SELECT 1")
                    return
                if operation=="snapshot":
                    admissions=(await conn.execute(select(Admission.task_id,Admission.digest).order_by(Admission.task_id))).all()
                    openings=(await conn.execute(select(Opening.task_id,Opening.candidate).order_by(Opening.task_id))).all()
                    superuser=(await conn.exec_driver_sql("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")).scalar() if dialect=="postgresql" else None
                    return {"admissions":dict(admissions),"openings":dict(openings),"database_superuser":superuser}
                if operation=="initialize":
                    if dialect=="postgresql":
                        await conn.exec_driver_sql("SELECT pg_advisory_xact_lock(884259)")
                    else:
                        await conn.exec_driver_sql("BEGIN IMMEDIATE")
                    await conn.run_sync(VerifierBase.metadata.create_all)
                    return
                if operation in {"admit","check"}:
                    task,digest=args
                    if operation=="admit":
                        await conn.execute(insert(Admission).values(task_id=task,digest=digest).on_conflict_do_nothing())
                    stored=(await conn.execute(select(Admission.digest).where(Admission.task_id==task))).scalar_one_or_none()
                    if stored!=digest:
                        raise PermissionError("unregistered or changed verifier admission")
                else:
                    task,candidate=args
                    await conn.execute(insert(Opening).values(task_id=task,candidate=candidate).on_conflict_do_nothing())
                    stored=(await conn.execute(select(Opening.candidate).where(Opening.task_id==task))).scalar_one()
                    if stored!=candidate:
                        raise PermissionError("holdout already opened for a different finalist or patch")
        finally:
            await engine.dispose()

    def initialize(self):sync(self._operation("initialize"))
    def ping(self):sync(self._operation("ping"))
    def snapshot(self):return sync(self._operation("snapshot"))
    def admit(self,task,digest):sync(self._operation("admit",task,digest))
    def check(self,task,digest):sync(self._operation("check",task,digest))
    def consume(self,task,candidate):sync(self._operation("consume",task,candidate))


class BoundLedger:
    """The same logical candidate id cannot be replayed with different code."""
    def __init__(self,ledger,diff_digest):self.ledger,self.diff_digest=ledger,diff_digest
    def consume(self,task,candidate):self.ledger.consume(task,candidate+"@"+self.diff_digest)
