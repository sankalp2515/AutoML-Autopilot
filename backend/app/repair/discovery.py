"""Pytest's own collection, executed in the candidate sandbox, not filename guesses."""
import json

from app.repair.oracle import common as cm
from app.repair.sandbox.base import SCRATCH, SandboxError, Upload

COLLECTOR = b'''import json, os, sys
sys.path.insert(0, os.getcwd())
import pytest
class Collector:
    def pytest_collection_finish(self, session):
        with open(sys.argv[1], "w", encoding="utf-8") as f:
            json.dump([item.nodeid for item in session.items], f)
result = pytest.main(sys.argv[2:] + ["--collect-only", "-q", "-p", "no:cacheprovider"], plugins=[Collector()])
raise SystemExit(result)
'''


def collect_tests(runner, env_id, contract) -> set[str]:
    command = cm.expand(contract.commands.test, cm.placeholders(contract))
    if command[:3] == ["python", "-m", "pytest"]:
        command = command[3:]
    else:
        raise SandboxError("pytest collection requires a declared pytest test command")
    output = f"{SCRATCH}/collection.json"
    result = runner.run(env_id, ["python", f"{SCRATCH}/collect.py", output, *command],
                        timeout_s=contract.limits.test_seconds,
                        uploads=[Upload(f"{SCRATCH}/collect.py", COLLECTOR)], collect=[output])
    if result.timed_out or result.exit_code not in (0, 5):
        raise SandboxError("pytest collection failed; inspect the test command and collection errors")
    try:
        ids = json.loads(runner.read_file(env_id, output))
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            raise ValueError()
        return set(ids)
    except (FileNotFoundError, ValueError, TypeError) as exc:
        raise SandboxError("pytest did not produce a valid collection record") from exc
