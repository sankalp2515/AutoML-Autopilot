import pytest
from unittest.mock import AsyncMock, patch

from app.agents.data_auditor import DataAuditorAgent
from app.core.state import AgentState


def _base_state() -> AgentState:
    return AgentState(
        run_id="test-run-002",
        user_goal="Predict customer churn",
        target_column="churned",
        task_type="binary_classification",
        exclude_columns=[],
        decision_log=[],
    )


def _mock_profile() -> dict:
    return {
        "shape": [10000, 22],
        "dtypes": {"age": "float64", "income": "float64", "churned": "int64"},
        "null_counts": {"age": 800, "income": 0},
        "null_pct": {"age": 8.0, "income": 0.0},
        "describe": {},
        "cardinality": {"country": 45, "plan_type": 3},
        "target_distribution": {0: 0.87, 1: 0.13},
        "sample_rows": [],
        "memory_mb": 12.5,
    }


def _mock_log_entry() -> dict:
    return {
        "agent": "data_auditor", "timestamp": "2026-01-01T00:00:00Z",
        "decision": "proceed", "reasoning": "ok", "code_executed": "", "result_summary": "",
    }


@pytest.mark.asyncio
async def test_data_auditor_usable_dataset():
    agent = DataAuditorAgent()

    mock_llm_response = {
        "verdict": "usable",
        "warnings": ["class imbalance: 87/13 split"],
        "decisions": [{"decision": "proceed with pipeline", "reasoning": "data quality acceptable"}],
    }

    with (
        patch.object(agent, "execute_code", new_callable=AsyncMock,
                     return_value={"success": True, "result": _mock_profile()}),
        patch.object(agent.llm, "complete_json", new_callable=AsyncMock, return_value=mock_llm_response),
        patch.object(agent, "_mark_step", new_callable=AsyncMock),
        patch.object(agent, "emit", new_callable=AsyncMock),
        patch.object(agent, "_log_decision", new_callable=AsyncMock, return_value=_mock_log_entry()),
        patch("app.agents.data_auditor.mlflow"),
    ):
        result = await agent.run(_base_state())

    assert result["audit_verdict"] == "usable"
    assert "data_audit" in result
    assert result["data_audit"]["shape"] == [10000, 22]


@pytest.mark.asyncio
async def test_data_auditor_abort_on_bad_data():
    agent = DataAuditorAgent()

    bad_profile = _mock_profile()
    bad_profile["shape"] = [30, 5]

    mock_llm_response = {
        "verdict": "abort",
        "warnings": ["only 30 rows — insufficient for ML"],
        "decisions": [{"decision": "abort pipeline", "reasoning": "30 rows is too few"}],
    }

    with (
        patch.object(agent, "execute_code", new_callable=AsyncMock,
                     return_value={"success": True, "result": bad_profile}),
        patch.object(agent.llm, "complete_json", new_callable=AsyncMock, return_value=mock_llm_response),
        patch.object(agent, "_mark_step", new_callable=AsyncMock),
        patch.object(agent, "emit", new_callable=AsyncMock),
        patch.object(agent, "_log_decision", new_callable=AsyncMock, return_value=_mock_log_entry()),
        patch("app.agents.data_auditor.mlflow"),
    ):
        result = await agent.run(_base_state())

    assert result.get("status") == "failed"
    assert result.get("audit_verdict") == "abort"


@pytest.mark.asyncio
async def test_data_auditor_sandbox_failure():
    agent = DataAuditorAgent()

    with (
        patch.object(agent, "execute_code", new_callable=AsyncMock,
                     return_value={"success": False, "error": "CSV parse error: bad encoding"}),
        patch.object(agent, "_mark_step", new_callable=AsyncMock),
        patch.object(agent, "emit", new_callable=AsyncMock),
    ):
        result = await agent.run(_base_state())

    assert result.get("status") == "failed"
    assert "Data profiling failed" in result.get("error", "")
