from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreateRunRequest(BaseModel):
    user_goal: str = Field(..., min_length=10, max_length=1000)
    exclude_columns: list[str] = Field(default_factory=list)
    fp_fn_preference: str | None = None
    interpretability_required: bool = False


class RerunRequest(BaseModel):
    # Clone a run with extra columns excluded (e.g. leaked features the Skeptic found).
    exclude_columns: list[str] = Field(default_factory=list)


class AgentStepOut(BaseModel):
    id: str
    agent_name: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None

    model_config = {"from_attributes": True}


class DecisionLogOut(BaseModel):
    id: str
    agent_name: str
    timestamp: datetime
    decision: str
    reasoning: str
    code_executed: str | None
    result_summary: str | None

    model_config = {"from_attributes": True}


class RunOut(BaseModel):
    id: str
    status: str
    dataset_filename: str
    user_goal: str
    task_type: str | None
    target_column: str | None
    primary_metric: str | None
    baseline_score: float | None
    final_score: float | None
    winner_model: str | None
    iteration_count: int
    mlflow_run_id: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    # Attached from the Trust report (Redis) when present — surfaced at a glance.
    trust_grade: str | None = None
    trust_score: float | None = None

    model_config = {"from_attributes": True}


class RunDetailOut(RunOut):
    agent_steps: list[AgentStepOut] = []
    decision_logs: list[DecisionLogOut] = []


class RunListOut(BaseModel):
    runs: list[RunOut]
    total: int


class ResultsOut(BaseModel):
    run_id: str
    task_type: str | None
    target_column: str | None
    primary_metric: str | None
    baseline_score: float | None
    final_score: float | None
    winner_model: str | None
    iteration_count: int
    evaluation_report: dict[str, Any] | None
    shap_top_features: list[str] | None
    artifact_paths: dict[str, str] | None
    meta: dict[str, Any] | None = None
