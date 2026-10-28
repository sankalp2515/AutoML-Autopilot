import pytest
from unittest.mock import AsyncMock, patch

from app.agents.problem_framer import ProblemFramerAgent
from app.core.state import AgentState


def _base_state(goal: str) -> AgentState:
    return AgentState(
        run_id="test-run-001",
        user_goal=goal,
        exclude_columns=[],
        interpretability_required=False,
        fp_fn_preference="",
        data_audit={"dtypes": {"age": "int64", "income": "float64", "churned": "int64"}},
        decision_log=[],
    )


def _mock_log_entry() -> dict:
    return {
        "agent": "problem_framer", "timestamp": "2026-01-01T00:00:00Z",
        "decision": "test", "reasoning": "test", "code_executed": "", "result_summary": "",
    }


@pytest.mark.asyncio
async def test_problem_framer_binary_classification():
    agent = ProblemFramerAgent()

    mock_llm_response = {
        "task_type": "binary_classification",
        "target_column": "churned",
        "primary_metric": "recall",
        "good_enough_threshold": 0.80,
        "reasoning": "Goal is to predict customer churn, binary outcome.",
        "decisions": [
            {"decision": "Task: binary_classification", "reasoning": "churned is a 0/1 column"},
            {"decision": "Metric: recall", "reasoning": "missing churner is worse than false alarm"},
        ],
    }

    with (
        patch.object(agent.llm, "complete_json", new_callable=AsyncMock, return_value=mock_llm_response),
        patch.object(agent, "_mark_step", new_callable=AsyncMock),
        patch.object(agent, "_update_run_field", new_callable=AsyncMock),
        patch.object(agent, "emit", new_callable=AsyncMock),
        patch.object(agent, "_log_decision", new_callable=AsyncMock, return_value=_mock_log_entry()),
        patch("app.agents.problem_framer.mlflow"),
    ):
        result = await agent.run(_base_state("Predict which customers will churn next month"))

    assert result["task_type"] == "binary_classification"
    assert result["target_column"] == "churned"
    assert result["primary_metric"] == "recall"
    assert result["good_enough_threshold"] == 0.80


@pytest.mark.asyncio
async def test_problem_framer_regression():
    agent = ProblemFramerAgent()

    mock_llm_response = {
        "task_type": "regression",
        "target_column": "house_price",
        "primary_metric": "rmse",
        "good_enough_threshold": 0.85,
        "reasoning": "Goal is to estimate a continuous price.",
        "decisions": [{"decision": "regression task", "reasoning": "house price is continuous"}],
    }

    with (
        patch.object(agent.llm, "complete_json", new_callable=AsyncMock, return_value=mock_llm_response),
        patch.object(agent, "_mark_step", new_callable=AsyncMock),
        patch.object(agent, "_update_run_field", new_callable=AsyncMock),
        patch.object(agent, "emit", new_callable=AsyncMock),
        patch.object(agent, "_log_decision", new_callable=AsyncMock, return_value=_mock_log_entry()),
        patch("app.agents.problem_framer.mlflow"),
    ):
        result = await agent.run(_base_state("Estimate house sale price from property features"))

    assert result["task_type"] == "regression"
    assert result["primary_metric"] == "rmse"


@pytest.mark.asyncio
async def test_problem_framer_appends_to_decision_log():
    agent = ProblemFramerAgent()

    mock_llm_response = {
        "task_type": "binary_classification",
        "target_column": "default",
        "primary_metric": "auc_roc",
        "good_enough_threshold": 0.80,
        "reasoning": "Loan default — binary outcome.",
        "decisions": [{"decision": "binary", "reasoning": "binary target"}],
    }

    existing_entry = {
        "agent": "previous_agent", "timestamp": "2026-01-01T00:00:00Z",
        "decision": "existing", "reasoning": "existing", "code_executed": "", "result_summary": "",
    }

    state = _base_state("Predict which loans will default")
    state["decision_log"] = [existing_entry]

    new_entry = {
        "agent": "problem_framer", "timestamp": "2026-01-01T00:00:01Z",
        "decision": "new", "reasoning": "new", "code_executed": "", "result_summary": "",
    }

    with (
        patch.object(agent.llm, "complete_json", new_callable=AsyncMock, return_value=mock_llm_response),
        patch.object(agent, "_mark_step", new_callable=AsyncMock),
        patch.object(agent, "_update_run_field", new_callable=AsyncMock),
        patch.object(agent, "emit", new_callable=AsyncMock),
        patch.object(agent, "_log_decision", new_callable=AsyncMock, return_value=new_entry),
        patch("app.agents.problem_framer.mlflow"),
    ):
        result = await agent.run(state)

    assert len(result["decision_log"]) == 2
    assert result["decision_log"][0]["agent"] == "previous_agent"
    assert result["decision_log"][1]["agent"] == "problem_framer"
