"""Persistence for investigations.

`autopilot_investigations` holds the current snapshot (fast reads for the UI).
`autopilot_events` is append-only: one row per state change, unique on
(investigation_id, seq), so the timeline can be replayed and a duplicate write is
rejected by the database rather than silently doubled.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
                        func)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AutopilotInvestigation(Base):
    __tablename__ = "autopilot_investigations"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key", name="uq_autopilot_idem"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="public", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scenario_id: Mapped[str] = mapped_column(String(32))     # "S02" or "upload:<12 hex>"
    status: Mapped[str] = mapped_column(String(24), index=True)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    # Worker dispatch (AUTOPILOT_EXECUTION=worker): runs are claimed with a time-bound
    # lease by compare-and-swap; an expired lease on a started run means the worker
    # died, and the run is failed safe (never resumed half-way, never "success").
    auto_run: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    steps_requested: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(96), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class AutopilotWorker(Base):
    """Heartbeat from each worker: liveness for /health and provider status for the
    API, which itself holds no provider keys."""
    __tablename__ = "autopilot_workers"

    worker_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    info: Mapped[dict] = mapped_column(JSON, default=dict)


class AutopilotEvent(Base):
    __tablename__ = "autopilot_events"
    __table_args__ = (UniqueConstraint("investigation_id", "seq", name="uq_autopilot_event_seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("autopilot_investigations.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(24))
    kind: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
