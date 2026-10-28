import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    # Multi-tenancy (Phase 4). "public" in single-tenant mode. Indexed for
    # per-tenant listing/quota. Child tables authorize via their run's tenant_id.
    tenant_id: Mapped[str] = mapped_column(String(64), default="public", index=True)

    # User inputs
    dataset_filename: Mapped[str] = mapped_column(String(255))
    dataset_path: Mapped[str] = mapped_column(String(512))
    user_goal: Mapped[str] = mapped_column(Text)
    exclude_columns: Mapped[list] = mapped_column(JSON, default=list)
    fp_fn_preference: Mapped[str | None] = mapped_column(Text, nullable=True)
    interpretability_required: Mapped[bool] = mapped_column(default=False)
    # "tabular" (default) | "timeseries" — which pipeline/studio runs this dataset
    pipeline: Mapped[str] = mapped_column(String(20), default="tabular")

    # Detected / computed
    task_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_column: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_metric: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Scores
    baseline_score: Mapped[float | None] = mapped_column(nullable=True)
    final_score: Mapped[float | None] = mapped_column(nullable=True)
    winner_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    iteration_count: Mapped[int] = mapped_column(default=0)

    # MLflow
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Error
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    agent_steps: Mapped[list["AgentStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="AgentStep.created_at"
    )
    decision_logs: Mapped[list["DecisionLog"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="DecisionLog.timestamp"
    )
    llm_calls: Mapped[list["LLMCall"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="LLMCall.created_at"
    )


class AgentStep(Base):
    __tablename__ = "agent_steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    agent_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run: Mapped["Run"] = relationship(back_populates="agent_steps")


class DecisionLog(Base):
    __tablename__ = "decision_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    agent_name: Mapped[str] = mapped_column(String(100))
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    decision: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[str] = mapped_column(Text)
    code_executed: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped["Run"] = relationship(back_populates="decision_logs")


class Deployment(Base):
    """A run's model promoted to a live prediction endpoint (Phase 3)."""
    __tablename__ = "deployments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | stopped
    n_predictions: Mapped[int] = mapped_column(Integer, default=0)
    deployed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    stopped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    run: Mapped["Run"] = relationship()


class PredictionLog(Base):
    """Every prediction served — the raw material for drift detection."""
    __tablename__ = "prediction_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    features: Mapped[dict] = mapped_column(JSON)
    prediction: Mapped[str] = mapped_column(String(255))
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LLMCall(Base):
    """One record per LLM API call — used for cost/latency observability."""
    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    agent_name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    # Estimated cost in USD (Groq free tier = $0, Anthropic = per token)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run: Mapped["Run"] = relationship(back_populates="llm_calls")
