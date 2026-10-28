"""Phase 1 — AST safety screen for generated (restricted) code.

The sandbox container is the primary boundary (no network, dropped caps, process
isolation). This screen is the secondary, in-process defense for LLM-written
code: it must reject imports outside the whitelist, banned builtins, and
attribute-walk escape hatches — while letting legitimate ML code through.

Imports the sandbox module by path (it lives outside the backend package).
"""

import importlib.util
import tempfile
from pathlib import Path

import pytest

_SANDBOX_MAIN = Path(__file__).resolve().parents[3] / "sandbox" / "main.py"


def _load_sandbox():
    spec = importlib.util.spec_from_file_location("sandbox_main", _SANDBOX_MAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Keep artifact writes inside a temp dir (execute_code makedirs DATA_DIR/run).
    mod.DATA_DIR = tempfile.mkdtemp(prefix="sandbox_test_")
    return mod


# The sandbox source lives in a sibling dir that is NOT mounted into the backend
# container — skip there (this suite runs from the repo root / locally instead).
pytest.importorskip("fastapi")
if not _SANDBOX_MAIN.exists():
    pytest.skip(f"sandbox source not available at {_SANDBOX_MAIN}", allow_module_level=True)

sandbox = _load_sandbox()


BLOCKED = [
    ("import os", "import"),
    ("import subprocess", "import"),
    ("import socket", "import"),
    ("from os import system", "import-from"),
    ("x = eval('1')", "name"),
    ("x = __import__('os')", "name"),
    ("breakpoint()", "name"),
    ("().__class__.__bases__", "attribute"),
    ("x = ().__class__.__subclasses__()", "attribute"),
    ("open('/etc/passwd')", "name"),
]

ALLOWED = [
    "import pandas as pd\nimport numpy as np",
    "from sklearn.ensemble import RandomForestClassifier",
    "import xgboost as xgb",
    "from imblearn.over_sampling import SMOTE",
    "RESULT = {'ok': sum([1, 2, 3])}",
]


@pytest.mark.parametrize("code,_kind", BLOCKED, ids=[c[0][:20] for c in BLOCKED])
def test_screen_blocks_unsafe(code, _kind):
    assert sandbox.screen_code(code) is not None, f"should have blocked: {code!r}"


@pytest.mark.parametrize("code", ALLOWED, ids=[c[:20] for c in ALLOWED])
def test_screen_allows_legitimate_ml(code):
    assert sandbox.screen_code(code) is None, f"should have allowed: {code!r}"


def test_execute_code_restricted_blocks_os_import():
    r = sandbox.execute_code("import os", "rid", 10, restricted=True)
    assert r["success"] is False
    assert "safety screen" in r["error"]


def test_execute_code_runs_plain_python():
    r = sandbox.execute_code("RESULT = 6 * 7", "rid", 10, restricted=False)
    assert r["success"] is True
    assert r["result"] == 42
