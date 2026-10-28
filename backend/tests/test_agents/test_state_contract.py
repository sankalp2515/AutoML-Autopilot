"""
Static state-contract guard.

Catches the highest-cost bug class in this codebase: an agent's run() returns a
dict key that is NOT declared in the AgentState TypedDict. LangGraph silently
drops undeclared keys, so the data never reaches downstream agents — a silent,
hard-to-trace failure (this is exactly what broke multilabel inference: the
preprocessor returned multilabel_binarizer_path / training_pipeline_path but
AgentState didn't declare them).

This test is fully static (ast only) — no sandbox, no LLM, no network — so it
runs in milliseconds and can never be flaky. It parses each agent module, finds
the run() method's `return {...}` dict literals, collects their string keys, and
asserts every key is declared in AgentState.
"""

import ast
import typing
from pathlib import Path

from app.core.state import AgentState

AGENTS_DIR = Path(__file__).resolve().parents[2] / "app" / "agents"

# Keys agents legitimately return that are runtime control flow, not state fields.
_CONTROL_KEYS = {"error", "status"}

_DECLARED = set(typing.get_type_hints(AgentState).keys()) | _CONTROL_KEYS


def _literal_return_keys(source: str) -> set[str]:
    """String keys from `return {...}` dict literals inside the agent's run() method.

    Scoped to run() only — module-level helpers (e.g. exporter's notebook-structure
    builders) legitimately return non-state dicts and must not be flagged.
    """
    keys: set[str] = set()
    tree = ast.parse(source)
    run_methods = [
        item
        for cls in tree.body if isinstance(cls, ast.ClassDef)
        for item in cls.body
        if isinstance(item, (ast.AsyncFunctionDef, ast.FunctionDef)) and item.name == "run"
    ]
    for run in run_methods:
        for node in ast.walk(run):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                for k in node.value.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
    return keys


def _agent_modules() -> list[Path]:
    return [
        p for p in AGENTS_DIR.glob("*.py")
        if p.name not in ("__init__.py", "base_agent.py", "orchestrator.py")
    ]


def test_agent_return_keys_are_declared_in_state():
    """Every dict key an agent returns must be a declared AgentState field."""
    violations: dict[str, set[str]] = {}
    for path in _agent_modules():
        returned = _literal_return_keys(path.read_text(encoding="utf-8"))
        undeclared = returned - _DECLARED
        if undeclared:
            violations[path.name] = undeclared

    assert not violations, (
        "Agent run() returns keys not declared in AgentState (LangGraph will "
        "silently drop them). Add them to app/core/state.py:\n"
        + "\n".join(f"  {f}: {sorted(keys)}" for f, keys in violations.items())
    )
