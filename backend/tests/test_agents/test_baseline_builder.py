import pytest
from unittest.mock import AsyncMock, patch

from app.agents.baseline_builder import BaselineBuilderAgent
from app.core.state import AgentState


def _base_state() -> AgentState:
    return AgentState(
        run_id="test-run-003",
        user_goal="Predict customer churn",
        target_column="churned",
        task_type="binary_classification",
        primary_metric="recall",
        exclude_columns=[],
        data_audit={"shape": [10000, 22]},
        decision_log=[],
    )


def _mock_sandbox_result() -> dict:
    return {
        "success": True,
        "result": {
            "baseline_score": 0.67,
            "dummy_score": 0.50,
            "baseline_model": "LogisticRegression",
            "score_std": 0.02,
            "n_features": 20,
            "n_samples": 10000,
            "baseline_path": "/data/test-run-003/artifacts/baseline_pipeline.pkl",
            "error_rate_on_train": 0.18,
            "metric_used": "roc_auc",
        },
    }


def _mock_log_entry() -> dict:
    return {
        "agent": "baseline_builder", "timestamp": "2026-01-01T00:00:00Z",
        "decision": "baseline=0.67", "reasoning": "logistic regression", "code_executed": "", "result_summary": "",
    }


@pytest.mark.asyncio
async def test_baseline_establishes_floor():
    agent = BaselineBuilderAgent()

    with (
        patch.object(agent, "execute_code", new_callable=AsyncMock, return_value=_mock_sandbox_result()),
        patch.object(agent, "_mark_step", new_callable=AsyncMock),
        patch.object(agent, "emit", new_callable=AsyncMock),
        patch.object(agent, "_log_decision", new_callable=AsyncMock, return_value=_mock_log_entry()),
        patch.object(agent, "_update_run_field", new_callable=AsyncMock),
        patch("app.agents.baseline_builder.mlflow"),
    ):
        result = await agent.run(_base_state())

    assert result["baseline_score"] == 0.67
    assert result["baseline_model"] == "LogisticRegression"
    assert result["current_score"] == 0.67
    assert result["iteration"] == 0
    assert result["iteration_scores"] == [0.67]
    assert len(result["decision_log"]) == 1


@pytest.mark.asyncio
async def test_baseline_handles_sandbox_failure():
    agent = BaselineBuilderAgent()

    with (
        patch.object(agent, "execute_code", new_callable=AsyncMock,
                     return_value={"success": False, "error": "ModuleNotFoundError: xgboost"}),
        patch.object(agent, "_mark_step", new_callable=AsyncMock),
        patch.object(agent, "emit", new_callable=AsyncMock),
    ):
        result = await agent.run(_base_state())

    assert result.get("status") == "failed"
    assert "Baseline training failed" in result.get("error", "")


@pytest.mark.asyncio
async def test_baseline_logs_improvement_over_dummy():
    agent = BaselineBuilderAgent()

    sandbox_result = _mock_sandbox_result()
    sandbox_result["result"]["baseline_score"] = 0.85
    sandbox_result["result"]["dummy_score"] = 0.50

    with (
        patch.object(agent, "execute_code", new_callable=AsyncMock, return_value=sandbox_result),
        patch.object(agent, "_mark_step", new_callable=AsyncMock),
        patch.object(agent, "emit", new_callable=AsyncMock),
        patch.object(agent, "_log_decision", new_callable=AsyncMock, return_value=_mock_log_entry()),
        patch.object(agent, "_update_run_field", new_callable=AsyncMock),
        patch("app.agents.baseline_builder.mlflow"),
    ):
        result = await agent.run(_base_state())

    assert result["baseline_score"] == 0.85
    assert result["current_score"] == 0.85


@pytest.mark.asyncio
async def test_baseline_regression_task():
    agent = BaselineBuilderAgent()

    sandbox_result = _mock_sandbox_result()
    sandbox_result["result"]["baseline_model"] = "Ridge"
    sandbox_result["result"]["metric_used"] = "neg_root_mean_squared_error"
    sandbox_result["result"]["baseline_score"] = 45230.5

    state = _base_state()
    state["task_type"] = "regression"
    state["primary_metric"] = "rmse"
    state["target_column"] = "price"

    with (
        patch.object(agent, "execute_code", new_callable=AsyncMock, return_value=sandbox_result),
        patch.object(agent, "_mark_step", new_callable=AsyncMock),
        patch.object(agent, "emit", new_callable=AsyncMock),
        patch.object(agent, "_log_decision", new_callable=AsyncMock, return_value=_mock_log_entry()),
        patch.object(agent, "_update_run_field", new_callable=AsyncMock),
        patch("app.agents.baseline_builder.mlflow"),
    ):
        result = await agent.run(state)

    assert result["baseline_model"] == "Ridge"
    assert result["baseline_score"] == 45230.5
