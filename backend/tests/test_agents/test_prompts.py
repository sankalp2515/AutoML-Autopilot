"""
P17 — prompt regression guard (static, no network).

Catches prompt edits that silently drop a JSON field the agent's run() later parses.
Each agent calls llm.complete_json(SYSTEM_PROMPT, ...) and reads specific keys; if a
prompt stops asking for one, the field goes missing at runtime. These assertions pin
the contract between prompt and parser.
"""

from app.agents.problem_framer import SYSTEM_PROMPT as FRAMER
from app.agents.data_auditor import SYSTEM_PROMPT as AUDITOR
from app.agents.preprocessor import SYSTEM_PROMPT as PREP
from app.agents.feature_engineer import SYSTEM_PROMPT as FEAT
from app.agents.model_selector import SYSTEM_PROMPT as SELECTOR


def _has_all(text: str, keys: list[str]) -> list[str]:
    return [k for k in keys if k not in text]


def test_problem_framer_prompt_requests_required_fields():
    missing = _has_all(FRAMER, ["task_type", "target_column", "primary_metric", "decisions"])
    assert not missing, f"problem_framer prompt missing keys: {missing}"


def test_data_auditor_prompt_requests_verdict():
    missing = _has_all(AUDITOR, ["verdict", "usable", "warn", "abort"])
    assert not missing, f"data_auditor prompt missing keys: {missing}"


def test_preprocessor_prompt_requests_strategies():
    missing = _has_all(PREP, ["imputation_strategy", "encoding_strategy", "scaling_strategy"])
    assert not missing, f"preprocessor prompt missing keys: {missing}"


def test_feature_engineer_prompt_requests_features():
    missing = _has_all(FEAT, ["proposed_features", "formula", "hypothesis"])
    assert not missing, f"feature_engineer prompt missing keys: {missing}"


def test_model_selector_prompt_requests_selection():
    missing = _has_all(SELECTOR, ["selected_models", "class", "initial_params"])
    assert not missing, f"model_selector prompt missing keys: {missing}"
