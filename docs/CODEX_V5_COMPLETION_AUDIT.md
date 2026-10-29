# Codex v5 completion audit — 30 October 2026

Branch: `codex/fixtures-bug-hardening`, based on `v5/main` at `c61da72`, with
`v5/frontend` integrated. This is a branch delivery, not a merge into `v5/main`.
The original checkout contained uncommitted agent changes; they were not copied,
overwritten or included in this work.

## Five additional repositories

| Repository      | Fault family                           | Visible failure                      | Tempting green patch rejected for                                  |
| --------------- | -------------------------------------- | ------------------------------------ | ------------------------------------------------------------------ |
| loan-scorer     | B01: preprocessing library API         | Removed OneHotEncoder argument       | Online/batch probability disagreement                              |
| refund-risk     | B03: target leakage                    | Insufficient recall                  | Depending on a post-delivery column unavailable at checkout        |
| credit-approval | B06: protected-group regression        | Insufficient aggregate F1            | Rural recall dropping despite an aggregate F1 gain                 |
| fraud-batch     | B09: serialization                     | Fresh-process artifact load fails    | Refit-on-load workaround fails independent online artifact loading |
| pricing-model   | B14: untrusted repository instructions | Zero denominator in serving features | Constant serving fallback disagrees with batch behavior            |

Each repository contains `repo/` with a validated contract, pinned requirements,
source, visible tests, synthetic training rows and unlabeled holdout features.
Labels, wrong/reference patches and proof expectations live only under `sealed/`.
The sample API automatically discovers the five additions alongside churn-service.

`scripts/generate_repair_fixtures.py` reproduces all five from fixed seeds. Each
has 600 training and 600 holdout rows with disjoint IDs. Region groups include at
least 30 holdout positives each. There is no real applicant/customer data.

`backend/tests/test_repair/test_fixture_suite.py` stages and pins each repository
through actual intake, reproduces the failing test, then checks both patches with
the platform software and ML oracles. `docs/benchmarks/repair/fixture-proofs/`
contains the measured receipts, actual snapshot SHA/digest and runner disclosure.
Proofs are **deterministic checker tests using authored patches**, not genuine
autonomous-agent recordings, generalization measurements or a matched-budget study.
Do not insert them into the replay player as if an agent produced the patches.

Concrete proof results: the credit wrong patch raises overall held-out F1 from
0.5882 to 0.8868 (paired delta 0.2986; recorded bootstrap CI [0.2353, 0.3562]),
while rural recall falls from 0.4833 to zero on 120 rural rows. It is rejected by
the regional guardrail. The refund wrong patch changes scores by as much as
0.8829 after the post-outcome column is blanked, and is rejected before labels open.
These are fixture measurements, not claims about real populations or fairness.

These implement the fault families, with deliberately narrower cases than every
detail in the narrative fault specs: B01's wrong patch changes serving probabilities
rather than using get_dummies; B09 exercises artifact loading/parity but does not yet
include the separate delete-training-data identity protocol. B14 policy denials are
tested directly; no paid model prompt-injection success rate is claimed.

## Bug fixes and boundaries

- BUG-01/06: production factories reject unisolated process execution and in-worker
  oracle fallback. Production needs Docker/sandbox and the verifier service. Tests
  retain explicit `ProcessRunner(..., allow_unisolated=True)` and local oracle helpers.
- BUG-02 was already resolved upstream: C2 and C4 are merged and their PR description
  files are absent.
- BUG-03: the agent refreshes a bounded file cache through sandbox-runner operations
  before file tools and syncs edits through write_files. A command's source changes
  are visible to subsequent tools. No sandbox-host work directory is needed.
- BUG-04: candidate clients get independent allocations with a shared usage ledger;
  candidates still execute concurrently. Prompt tokens are reported after calls,
  so each candidate may overshoot by one request. This is not strict pre-tokenization.
- BUG-05 is **partial**: online scoring executes in a fresh sandbox branch, without
  the batch prediction file, and trusted verifier code computes alignment, tolerances
  and differences. Candidate-returned `max_abs_diff`/verdict fields have no authority.
  Legacy sealed pytest is disabled outside tests rather than pretending that importing
  candidate code beside hidden tests is secure. A general separate-process/server
  sealed-test protocol remains to be built. Containers do not make arbitrary Python
  imports a safe boundary, and parity alone cannot detect every adversarial model.
- BUG-07: unsupported extensions, binaries and invalid UTF-8 are listed as skipped
  and refused by candidate screening; text replacement decoding no longer hides them.
- BUG-08: `scripts/run_repair.py` now uses sample intake and the durable task API,
  not a fake zero SHA or an obsolete direct agent constructor. It doesn't load dotenv.
- BUG-09 is **partial**: the planner receives bounded failing-test source, traceback
  files, serving entrypoints and imported local modules, explicitly as untrusted data.
  Actual hypothesis quality across live model runs is still unmeasured.
- BUG-10: README identifies v5's checks as model-proposed, not information-gain-ranked.
  The older data investigator's selector is a separate capability.
- BUG-11: slow tests have their own CI job; deterministic process-runner tests may opt
  into a bounded, opaque training artifact cache. Keys include every input/patch byte,
  contract and installed runtime versions. Candidate artifacts are never unpickled
  by the cache. The cache is off for live work and invalidated before arbitrary tests.
  Process-runner numerical libraries use one thread to avoid host oversubscription,
  and unrelated host pytest plugins are not auto-loaded. Explicit repository pytest
  plugins can still be declared.
- BUG-12 is **partial**: the [official Nebius cookbook](https://github.com/nebius/token-factory-cookbook/blob/main/models/nemotron/README.md)
  confirms the documented tier IDs. Super casing was corrected to
  `nvidia/nemotron-3-super-120b-a12b`; mapping and 404 behavior have offline tests.
  No successful account-level provider call was made. NVIDIA's configured Ultra
  coding fallback remains explicit; cheaper-tier availability/cost is not inferred.
- BUG-13: the finish gate uses pytest's actual collection and configured filename/
  function patterns. A file merely named `test_*` does not count if pytest ignores it.
  JUnit node mapping uses collected file paths rather than filename-prefix guesses.

These remaining criteria stay **partial** in the backlog, not falsely checked done.

## What remains in the frontend

The frontend's implemented screens/components are described in
[FRONTEND_COMPLETION.md](FRONTEND_COMPLETION.md) and
[FRONTEND_DESIGN.md](FRONTEND_DESIGN.md). They are complete as UI integrations for
published data, with the following end-to-end dependencies:

1. **FE-06:** editable per-task candidate/token caps and a credible pre-run estimate
   require backend admission/budget fields and validation. Current inputs honestly
   show server policy; the form does not send unsupported fields.
2. **FE-07:** publish candidate steps/source diffs/usage as they happen. The existing
   worker persists some artifacts only at completion, so the UI cannot show true
   continuous diff and token meters during that interval.
3. **FE-02/13:** supply EV-03/04 measured generic/full/no-ML/no-Tavily results before
   displaying a generic-agent false-accept headline or benchmark cost per verified fix.
4. **FE-03:** add a genuine agent-produced leakage rejection recording. The checker
   proofs delivered here do not substitute for that EV-02 requirement.
5. **FE-20 (P2):** implement organization membership/roles/invitations and scoped team
   APIs before enabling teams. Existing optional sign-in is not organization access control.
6. Finish real-service delivery, retention, auth and clean-machine launch acceptance.
   The previous UI suite used mocks for paid/external writes and a real no-inference
   intake smoke. These checks do not establish the full hosted journey.

This integration also corrects an earlier packaging omission: the broad `runs/`
ignore rule excluded `frontend/app/repair/runs/page.tsx` from the first frontend
commit even though it existed in the test worktree. The page is now explicitly
tracked, and the ignore rule targets `/runs/` at the repository root. The v5 browser
suite is included in CI so a clean checkout tests this route.

The backlog now has a status/remarks column for every BUG and FE item. Checked
**Branch** items are not labelled **Done in v5/main**. Backend rows not audited to
their entire acceptance criterion remain unchanged rather than bulk-marked complete.

## Validation

Run with no provider credentials and dotenv disabled in the test bootstrap:

```powershell
$env:PYTHONPATH=(Resolve-Path backend).Path
$env:REPAIR_TEST_TRAIN_CACHE='1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -c "from pydantic_settings.sources import DotEnvSettingsSource; DotEnvSettingsSource.__call__=lambda self: {}; import pytest; raise SystemExit(pytest.main(['-p','pytest_asyncio.plugin','backend/tests/test_repair','-m','not slow','-q']))"
$env:REPAIR_PROOF_OUTPUT=(Join-Path (Get-Location) 'docs/benchmarks/repair/fixture-proofs')
# Use the same dotenv-disabled bootstrap above, replacing 'not slow' with 'slow'.
```

CI runs a fast repair lane and a slow proofs/agent lane, and uploads proof artifacts.
The browser CI lane now includes both v4 and v5 suites. Final local results are
recorded below after completion. No model-provider requests, remote Git writes,
pushes, deployment or paid inference are part of this validation.

### Completed local checks

- Fast repair lane excluding the Docker module: **148 passed, 17 skipped, 21 deselected**.
  Windows/POSIX differences and unavailable Postgres account for the skips; this is
  not a local Postgres validation.
- The initial full fast run had **167 passed, 18 skipped, 2 Docker startup timeouts**.
  Both failing Docker cases passed on targeted retry, without changing assertions.
  This is recorded as a retry, not as a single uninterrupted green full run.
- Focused BUG regression file: **17 passed** (overlaps the fast lane).
- Production frontend build and TypeScript checks passed, including the previously
  omitted Runs route. Browser suites: **23 passed** (14 v5, 9 legacy), including
  unsupported-file rejection and the existing 38 v5 accessibility audits.
- Actual API intake browser checks: **2 passed**. All six samples render; credit
  intake validates its regional contract. No task inference was started.
- Full slow repair lane: **20 passed, 1 failed, 194 deselected**, in 20m46s.
  The failure was an existing assertion expecting the phrase `existing test files`;
  rejection behavior was correct. The message now preserves that phrase and a
  targeted rerun of the entire seven-case policy test passed: **7 passed**.
  All five new three-way proofs passed.

The counts above overlap; they must not be summed into a unique-test claim.
