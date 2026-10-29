"""Code oracle (card K2): did the patch fix the declared failure without breaking or gaming tests?

Order: static diff policy → apply to a pristine copy → visible tests (fail-to-pass and
pass-to-pass against the unpatched baseline) → sealed tests. The baseline is measured by
the oracle on the unpatched snapshot, never taken from the agent.
"""
from __future__ import annotations

import re
import os
from dataclasses import dataclass
from pathlib import Path

from app.repair.contracts import (ECC, CheckStatus, CodeReceipt, Decision, RepoContract, SealedManifest)
from app.repair.oracle import common as cm
from app.repair.sandbox.base import SCRATCH, SandboxError, SandboxRunner, Upload

PASS, FAIL, INCON = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.INCONCLUSIVE
# Ways to make a failing test stop failing without fixing anything.
_GAMING = re.compile(r"pytest\.(mark\.)?(skip|xfail|skipif|importorskip)\b|pytest\.skip\(|__test__\s*=\s*False|"
                     r"collect_ignore|pytest_collection_modifyitems|sys\.exit\(|os\._exit\(")


@dataclass
class Baseline:
    """Visible-test outcomes of the unpatched snapshot. Measured once per task."""
    outcomes: dict[str, str]
    parsed: bool


def _test_argv(contract: RepoContract) -> list[str]:
    return cm.expand(contract.commands.test, cm.placeholders(contract))


def measure_baseline(runner: SandboxRunner, base_dir: str, contract: RepoContract) -> Baseline:
    with cm.environment(runner, base_dir, contract) as env:
        t = cm.run_pytest(runner, env, _test_argv(contract), timeout_s=contract.limits.test_seconds,
                          report=f"{SCRATCH}/base.xml")
    return Baseline(t.outcomes, t.parsed)


def _policy(ecc: ECC, base_dir: str, diff: str) -> list:
    """Static checks on the diff itself. Returns failing checks (empty list = PASS)."""
    problems = []
    changes = cm.parse_diff(diff)
    if not changes:
        return [cm.check("diff_policy", FAIL, "the candidate changes nothing", code="E_EMPTY_DIFF")]
    n_lines = sum(len(c.added_lines) + c.removed for c in changes)
    base = Path(base_dir)
    for c in changes:
        p = c.path
        if c.binary:
            problems.append(f"{p}: binary, rename or copy is not a plain edit")
        if not cm.matches(p, ecc.allowed_edit_roots):
            problems.append(f"{p}: outside allowed edit roots {ecc.allowed_edit_roots}")
        if cm.matches(p, ecc.immutable):
            problems.append(f"{p}: immutable by contract")
        existed = (base / p).is_file()
        is_test = Path(p).name.startswith("test_") or Path(p).name.endswith("_test.py")
        if ecc.immutable_existing_tests and existed and is_test:
            problems.append(f"{p}: existing test files may not be edited or deleted")
        if is_test or Path(p).name == "conftest.py":
            gamed = [ln.strip() for ln in c.added_lines if _GAMING.search(ln)]
            if gamed:
                problems.append(f"{p}: adds test-skipping or collection-altering code: {gamed[0][:80]}")
    if n_lines > ecc.limits.max_diff_lines:
        problems.append(f"diff has {n_lines} changed lines; limit {ecc.limits.max_diff_lines}")
    if problems:
        return [cm.check("diff_policy", FAIL, "; ".join(problems[:6]), code="E_DIFF_POLICY", problems=problems)]
    return []


def verify(runner: SandboxRunner, *, base_dir: str, contract: RepoContract, ecc: ECC, task_id: str,
           candidate_id: str, diff: str, baseline: Baseline, sealed_dir: str | None = None,
           manifest: SealedManifest | None = None) -> CodeReceipt:
    checks: list = []
    ddig = cm.diff_digest(diff)

    def receipt(decision: Decision, reasons: list[str]) -> CodeReceipt:
        return CodeReceipt(task_id=task_id, candidate_id=candidate_id, contract_digest=ecc.contract_digest,
                           diff_digest=ddig, checks=checks, decision=decision, reasons=reasons,
                           runner=runner.name).sealed()

    if not ecc.intact():
        checks.append(cm.check("contract_integrity", FAIL, "engineering change contract digest does not match"))
        return receipt(Decision.REJECTED, ["E_CONTRACT_TAMPERED"])
    checks.append(cm.check("contract_integrity", PASS, "contract digest verified", digest=ecc.contract_digest))

    bad = _policy(ecc, base_dir, diff)
    if ecc.immutable_existing_tests:
        discovered = {node.split("::", 1)[0].casefold() for node in baseline.outcomes}
        changed_tests = [change.path for change in cm.parse_diff(diff)
                         if change.path.casefold() in discovered and (Path(base_dir) / change.path).is_file()]
        if changed_tests:
            bad.append(cm.check("diff_policy", FAIL, "existing test files collected by pytest may not be edited or deleted",
                                files=changed_tests, code="E_EXISTING_TEST"))
    if bad:
        checks.extend(bad)
        return receipt(Decision.REJECTED, ["E_DIFF_POLICY"])
    checks.append(cm.check("diff_policy", PASS, "edits stay inside allowed paths; no test gaming"))

    try:
        files = cm.apply_diff(base_dir, diff)
    except ValueError as e:
        checks.append(cm.check("diff_applies", FAIL, str(e)))
        return receipt(Decision.REJECTED, ["E_DIFF_DOES_NOT_APPLY"])
    checks.append(cm.check("diff_applies", PASS, f"applied to the pristine base ({len(files)} files)"))

    if not baseline.parsed:
        checks.append(cm.check("baseline", INCON, "could not read the baseline test report"))
        return receipt(Decision.INCONCLUSIVE, ["E_BASELINE_UNREADABLE"])
    f2p = ecc.success.fail_to_pass
    not_failing = [t for t in f2p if baseline.outcomes.get(t) not in ("failed", "error")]
    if not_failing:
        checks.append(cm.check("baseline", INCON, "declared failing tests do not fail on the base commit",
                               tests=not_failing))
        return receipt(Decision.INCONCLUSIVE, ["E_TASK_NOT_REPRODUCED"])
    checks.append(cm.check("baseline", PASS, f"{len(f2p)} declared failing test(s) reproduced on the base"))

    try:
        with cm.environment(runner, base_dir, contract, files) as env:
            t = cm.run_pytest(runner, env, _test_argv(contract), timeout_s=contract.limits.test_seconds,
                              report=f"{SCRATCH}/visible.xml")
            if not t.parsed:
                checks.append(cm.check("visible_tests", FAIL, "test run produced no report (crash or timeout)",
                                       exit_code=t.result.exit_code, timed_out=t.result.timed_out,
                                       tail=cm.tail(t.result)))
                return receipt(Decision.REJECTED, ["E_TESTS_DID_NOT_RUN"])
            still = [x for x in f2p if t.outcomes.get(x) != "passed"]
            checks.append(cm.check("fail_to_pass", FAIL if still else PASS,
                                   f"{len(f2p) - len(still)}/{len(f2p)} declared failing tests now pass",
                                   not_passing=still))
            base_pass = {k for k, v in baseline.outcomes.items() if v == "passed"}
            broken = sorted(base_pass - t.passed)
            new_skips = sorted(k for k, v in t.outcomes.items() if v == "skipped" and baseline.outcomes.get(k) != "skipped")
            fewer = len(t.outcomes) < len(baseline.outcomes)
            ok = not broken and not new_skips and not fewer
            checks.append(cm.check("pass_to_pass", PASS if ok else FAIL,
                                   "all previously passing tests still pass" if ok else
                                   "previously passing tests broke, were skipped, or disappeared",
                                   broken=broken[:20], newly_skipped=new_skips[:20],
                                   collected_base=len(baseline.outcomes), collected_candidate=len(t.outcomes),
                                   added_tests=sorted(set(t.outcomes) - set(baseline.outcomes))[:20]))

            if manifest and manifest.tests and sealed_dir:
                checks.append(_sealed(runner, env, contract, Path(sealed_dir), manifest))
    except SandboxError as e:
        checks.append(cm.check("sandbox", INCON, f"execution environment failed: {e}"))
        return receipt(Decision.SYSTEM_FAILURE, ["E_SANDBOX"])

    statuses = [c.status for c in checks]
    reasons = [f"E_{c.name.upper()}" for c in checks if c.status is not PASS]
    if FAIL in statuses:
        return receipt(Decision.REJECTED, reasons)
    if INCON in statuses:
        return receipt(Decision.INCONCLUSIVE, reasons)
    return receipt(Decision.VERIFIED, [])


def _sealed(runner: SandboxRunner, env: str, contract: RepoContract, sealed: Path, m: SealedManifest):
    """Sealed tests run against the candidate after the oracle builds its own artifacts.
    Only test files are uploaded; labels never are."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return cm.check("sealed_tests", INCON, "legacy in-process sealed pytest is disabled in production; use verifier-owned prediction probes")
    ph = cm.placeholders(contract)
    for kind in ("train", "predict"):
        cmd = getattr(contract.commands, kind)
        if cmd and any("{artifacts}" in v or "{predictions}" in v for v in m.env.values()):
            r = runner.run(env, cm.expand(cmd, ph), timeout_s=contract.limits.train_seconds)
            if r.exit_code != 0:
                return cm.check("sealed_tests", FAIL, f"{kind} failed before sealed tests could run",
                                exit_code=r.exit_code, tail=cm.tail(r))
    src = sealed / m.tests
    uploads = [Upload(f"{SCRATCH}/sealed_tests/{p.relative_to(src).as_posix()}", p.read_bytes())
               for p in sorted(src.rglob("*.py"))]
    env_vars = {k: cm.expand(v, ph)[0] if v else v for k, v in m.env.items()}
    t = cm.run_pytest(runner, env, ["python", "-m", "pytest", "-q", f"{SCRATCH}/sealed_tests"],
                      timeout_s=contract.limits.test_seconds, report=f"{SCRATCH}/sealed.xml",
                      uploads=uploads, env=env_vars)
    if not t.parsed or not t.outcomes:
        return cm.check("sealed_tests", FAIL, "sealed tests did not run", tail=cm.tail(t.result))
    failed = sorted(k.split("::")[-1] for k, v in t.outcomes.items() if v != "passed")
    return cm.check("sealed_tests", FAIL if failed else PASS,
                    f"{len(t.outcomes) - len(failed)}/{len(t.outcomes)} hidden tests pass", failed=failed)
