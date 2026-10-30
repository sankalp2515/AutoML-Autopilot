# Frontend, fixtures and evidence completion

Branch: `codex/fixtures-bug-hardening`. This follow-up completes frontend integration
rather than replacing the established visual system. Hosting and Nebius credentials
remain owner scope. NVIDIA developer inference was authorized during this task;
NVIDIA execution is never relabelled as Nebius execution.

## Acceptance mapping

- FE-06: lower-only per-task candidate/coding-token limits, server estimate, invalid
  input refusal, API validation and admission digest binding. Other limits remain
  server policy. The estimate is a nominal token ceiling, not a hard invoice limit;
  prompt usage can arrive after a call. NVIDIA costs are Nebius-rate equivalents.
- FE-07: persisted candidate.progress and usage.updated events drive source previews,
  tool excerpts, candidate tabs, tier token meters and elapsed/tool counts before
  completion. Previews are capped at 64 KB; frozen final patches remain separate.
  Existing task fencing and resumable event sequence apply to these writes.
- FE-02/13: checker matrix and two published-task feasibility results are visible.
  The four-arm table imports only a complete measured study. Counts, rates, costs,
  failures and unresolved acceptances are distinct. No authored patch is an agent run.
- FE-03: existing genuine churn replays retained. New genuine isolated churn and loan pilot recordings are bundled from actual model reports with source digests and runner/provider provenance. A specific leakage rejection cannot be promised before observation.
- EV-01: the five required fault-family proofs satisfy the card's three-way criterion
  and run in CI. The B01 supplement also proves dummy-column misalignment rejection and unseen-category acceptance. The B09 supplemental protocol deletes training rows, checks the
  artifact SHA remains unchanged, checks repeated predictions and 50 fresh-process
  online scores. Narrative examples outside the card's acceptance remain examples.
- EV-03: four-arm executable harness with identical admitted budgets and external
  checkers, seeds, resumable artifact slots, denominators, Wilson intervals, hashes,
  explicit failures and no invented costs. Generic and no-ML arms accept by code;
  evaluation still grades their accepted patches independently with ML checks.
- EV-05/06: two pinned public MLOps-Bench task patterns, exact source hashes/licenses,
  feasibility proofs and separate export/scoring harness. Baseline new-behavior
  tests fail 0/3; preserved behavior passes 4/4; reference passes 7/7 per task.
  These are public repository-grounded contract artifacts, not full real-repo pilots,
  autonomous results, hidden-test results or full-suite leaderboard replication.

## Reproduction

```powershell
python scripts/evaluate_repair.py plan --out <study-directory>
# Model calls require explicit opt-in; use NVIDIA count limit or a dollar estimate cap.
python scripts/evaluate_repair.py run --allow-model-calls --max-runs 72 --out <study-directory>
python scripts/evaluate_repair.py summarize --out <study-directory>
python scripts/evaluate_mlops_subset.py prove --out docs/benchmarks/repair/mlops-subset-proof.json
python scripts/publish_repair_evidence.py
# Add --comparison <complete-study>/summary.json only after a genuine study completes.
```

The runner must be isolated and the verifier service configured. Scripts do not
load dotenv, and credentials never enter committed plans/results. The temporary
operator bootstrap used for local evaluation consumes authorized credentials in
memory outside the repository and starts a separate local verifier.

## Scope and limits

The published [MLOps-Bench instructions](https://github.com/oracle-samples/mlops-bench)
separate agent workspaces from oracle/evaluator material. The subset export contains
only problem.json and repo/. The Python scorer is benchmark feasibility tooling,
not a hostile-code security boundary. Production arbitrary-code sealed verification
remains the separate BUG-05 gap.

NVIDIA developer access is [free for prototyping subject to account limits](https://docs.api.nvidia.com/nim/docs/product).
The successful Ultra completion and failed cheaper-tier probes are recorded in
`docs/benchmarks/repair/nvidia-availability.json`. Catalog listing is not proof of
successful account access. Extra user-authorized credentials share a thread-safe
cursor, retain independent Retry-After cooldowns, and never appear in call records.
No provider is silently changed. A 404 is not hidden by a model substitution.

The six pilots and 15/72 comparison outcomes, outage blocker and exact provenance limitations are documented in [the live evaluation report](benchmarks/repair/LIVE_EVALUATION.md). The remaining 57 comparison slots and a genuine leakage rejection are evidence blockers, not missing UI components. No comparative headline has been published.

Progress is persisted as the existing `diagnostic.executed` event with `data.kind=telemetry`, version 1 and `telemetry_type` identifying candidate.progress or usage.updated. The UI normalizes this envelope; original report event types remain supported. Frozen interfaces were preserved. A future top-level event-type addition must be an explicit protocol change.

## Runner inventory extension

The frozen contracts.py and sandbox/base.py interfaces were not changed. The local
runner implements an optional bounded `inventory(env_id)` file RPC, used by the
controller mirror when available; no host path leaves the runner. It skips symlinks,
hardlinks, secrets, sealed/platform folders and caps file count. Existing jailed
read_file performs byte reads, with mirror size/path validation retained. Runners
without this optional method use the existing stdlib-adapter fallback. A future
formal capability negotiation belongs in an interface ADR, not a silent edit of the
frozen protocol. Tests exercise the extension without executing candidate code.

## Validation

- Production Next.js build: passed, including type/lint checks and 20 static pages.
- Repair fast suite: 205 passed, 19 skipped, 24 slow cases deselected. One Docker
  fork/artifact test exceeded its 10-second limit under concurrent load; its isolated
  rerun passed together with the completion tests (12 passed, one fixture helper skipped).
- Scripted agent suite: three passed. Durable worker test with persisted telemetry:
  passed. The frozen Event enum is exercised through the real service, not a UI mock.
- B01 supplementary dummy/unseen-category test: passed. B09 artifact protocol:
  passed. The five original three-way proof artifacts remain in the repository/CI.
- Pinned public subset: both feasibility protocols passed, 7/7 reference checks each.
  One earlier repeated run had a collection error; a fresh complete run passed.
  The harness now retains bounded diagnostics for collection failures.
- Browser contracts: 23 passed plus the new genuine-recording case; three opt-in
  API cases skipped in that offline run. The updated persisted-telemetry envelope
  and both new isolated recordings passed a separate two-case browser check.
- Real API browser integration: five passed, including accounts/roles, six-fixture
  intake, contract review and source-to-verified-receipt with edited task limits.
  This uses a real worker/checkers with scripted inference and is not NVIDIA proof.
  The final preview uses the same-origin production proxy. An earlier direct-URL
  preview failed cross-origin requests; no production CORS policy was relaxed.
- Exact-value scan against all five supplied provider/research credentials: no
  matches in changed or newly added repository files. Frozen interfaces unchanged.

No push or main-branch commit was performed. Local checks do not establish a clean
host installation, external OAuth/App installation, Nebius execution, or the
remaining production verifier/transactional-spend gaps elsewhere in the backlog.
