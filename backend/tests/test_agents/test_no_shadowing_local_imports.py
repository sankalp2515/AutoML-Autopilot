"""Guard against the closure/local-import shadowing bug family.

Found live by the full E2E tier (2026-08-22), not by any unit test:

    model_selector crashed: NameError: cannot access free variable
    'metric_registry' where it is not associated with a value in enclosing scope

Root cause. A function-local `from app.core import metric_registry` rebinds that
name as a LOCAL of the enclosing function. When the same function also defines a
nested closure (`render`) that reads the name, Python resolves it as a free
variable in the enclosing scope — which is unbound at the moment the closure runs.
The module-level import at the top of the file is shadowed and does not help.

This is silent at import time, invisible to `ast.parse`, invisible to every test
that does not execute that specific closure, and it takes down a whole pipeline
step at runtime. So: a static rule.

THE RULE. Inside a function that defines a nested function, do not re-import a
name that is already imported at module level. Use the module-level import, or
alias the local one (`as _mreg`) so it cannot shadow.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

AGENTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "app" / "agents"
CORE_DIR = pathlib.Path(__file__).resolve().parents[2] / "app" / "core"
PY_FILES = sorted(
    [p for p in AGENTS_DIR.glob("*.py") if p.name != "__init__.py"]
    + [p for p in CORE_DIR.glob("*.py") if p.name != "__init__.py"]
)


def _module_level_names(tree: ast.Module) -> set[str]:
    """Names bound by imports at module scope."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _functions_with_nested_defs(tree: ast.Module):
    """Yield every function that contains a nested function definition."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        has_nested = any(
            isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            for child in ast.walk(node)
            if child is not node
        )
        if has_nested:
            yield node


def _local_import_bindings(func: ast.AST):
    """(name, lineno) for every import binding made inside a function body.

    Nested function bodies are skipped: an import inside the closure itself binds
    in the closure's own scope and cannot shadow the enclosing one.
    """
    out = []
    nested = {n for n in ast.walk(func)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not func}
    nested_nodes = {id(c) for f in nested for c in ast.walk(f)}
    for node in ast.walk(func):
        if node is func or id(node) in nested_nodes:
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                out.append((alias.asname or alias.name.split(".")[0], node.lineno))
    return out


@pytest.mark.parametrize("path", PY_FILES, ids=lambda p: p.name)
def test_no_local_import_shadows_a_module_level_one_inside_a_closure_scope(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_names = _module_level_names(tree)

    offenders = []
    for func in _functions_with_nested_defs(tree):
        for name, lineno in _local_import_bindings(func):
            if name in module_names:
                offenders.append(f"{path.name}:{lineno} — '{name}' is imported at module "
                                 f"level but re-imported inside '{func.name}', which "
                                 f"defines a nested function")

    assert not offenders, (
        "Local import shadows a module-level name in a function that has a closure.\n"
        "Any closure reading that name will raise NameError at runtime.\n"
        "Fix: drop the local import, or alias it (e.g. `as _mreg`).\n\n"
        + "\n".join(offenders)
    )


def test_the_rule_actually_catches_the_original_bug():
    """Meta-test: feed the checker the exact shape that broke model_selector and
    assert it fires. A guard that cannot fail is not a guard."""
    bad = '''
from app.core import metric_registry

def outer(state):
    from app.core import metric_registry
    def render():
        return metric_registry.scorer_prelude("auc_roc", "binary_classification")
    return render()
'''
    tree = ast.parse(bad)
    module_names = _module_level_names(tree)
    hits = [
        name
        for func in _functions_with_nested_defs(tree)
        for name, _ in _local_import_bindings(func)
        if name in module_names
    ]
    assert "metric_registry" in hits


def test_the_rule_allows_an_aliased_local_import():
    """`as _mreg` cannot shadow, so it must NOT be flagged — otherwise the guard
    is noise and someone will delete it."""
    ok = '''
from app.core import metric_registry

def outer(state):
    from app.core import metric_registry as _mreg
    def render():
        return metric_registry.DEFAULT_K + _mreg.DEFAULT_K
    return render()
'''
    tree = ast.parse(ok)
    module_names = _module_level_names(tree)
    hits = [
        name
        for func in _functions_with_nested_defs(tree)
        for name, _ in _local_import_bindings(func)
        if name in module_names
    ]
    assert hits == []
