"""Durable repair records. Worker mutations go through RepairService fencing."""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Task(Base):
    __tablename__ = "repair_tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    ecc: Mapped[dict] = mapped_column(JSON)
    event_seq: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    lease_owner: Mapped[str | None] = mapped_column(String(96), nullable=True)
    lease_until: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float)
    spec: Mapped[dict | None] = mapped_column(JSON, nullable=True)      # snapshot, sealed ref, mode, source
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)    # RepairReport (no labels, no keys)


class Candidate(Base):
    __tablename__ = "repair_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("repair_tasks.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    diff: Mapped[str] = mapped_column(Text, default="")


class Attempt(Base):
    __tablename__ = "repair_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("repair_tasks.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("repair_candidates.id", ondelete="CASCADE"))
    fencing_token: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)


class Receipt(Base):
    __tablename__ = "repair_receipts"
    __table_args__ = (UniqueConstraint("candidate_id", "kind", name="uq_repair_receipt_kind"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("repair_tasks.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("repair_candidates.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON)


class Event(Base):
    __tablename__ = "repair_events"
    __table_args__ = (UniqueConstraint("task_id", "seq", name="uq_repair_event_seq"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("repair_tasks.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)


class RepairWorker(Base):
    __tablename__ = "repair_workers"
    id: Mapped[str] = mapped_column(String(96), primary_key=True)
    beat: Mapped[float] = mapped_column(Float)
    info: Mapped[dict | None] = mapped_column(JSON, nullable=True)


def ensure_columns(sync_conn) -> None:
    """Add v5 columns to a repair_tasks table created before they existed (idempotent)."""
    from sqlalchemy import inspect, text
    insp = inspect(sync_conn)
    if "repair_tasks" not in insp.get_table_names():
        return
    have = {c["name"] for c in insp.get_columns("repair_tasks")}
    for col in ("spec", "report"):
        if col not in have:
            kind = "JSON" if sync_conn.dialect.name == "postgresql" else "TEXT"
            sync_conn.execute(text(f"ALTER TABLE repair_tasks ADD COLUMN {col} {kind}"))
