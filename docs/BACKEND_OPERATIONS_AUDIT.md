# Backend and operations re-audit — 2026-10-30

This audit covers BUG-05, BUG-09, BE-01 through BE-25 and IN-01 through IN-08 on `codex/fixtures-bug-hardening`. Changes are local; no main commit, push, existing-stack modification or Nebius inference was performed. The frozen repair contracts and sandbox base protocol were preserved. This is implementation/test evidence, not a certification that all production and submission gates are satisfied.

## BUG-05: independent code and ML evidence

Supported sealed artifact tests now execute in an empty independent container, on the same immutable dependency image. Only allowlisted inert JSON/CSV/text exports cross into it. Candidate code, conftest, pytest configuration, pickles and fabricated PASS text cannot author the trusted result. A separate sandbox capability token authorizes this route; worker credentials cannot invoke it. Real Docker tests exercise a fake verdict, poisoned conftest, withheld sibling input and a correct exported value. Batch/online parity remains computed by the verifier from raw observations using independent candidate branches.

Legacy sealed pytest that imports candidate Python is **unsupported in production** and returns INCONCLUSIVE. Arbitrary model execution cannot be made trustworthy merely by moving an importing Python script. Bundle owners must adopt `sealed/external-probes.json` version 1, kind `artifact-tests`, with `exports` mapping destination text filenames to contract-relative source paths. Trusted tests read `AP_INPUT_DIR`; no labels or trusted Python are uploaded to candidate execution. Dependencies/base image remain part of the trusted computing base.

## BUG-09: source context and measured diagnosis quality

The planner receives failure nodes, compact real traceback, pinned requirements, train/predict modules, serving entrypoints and relevant relative imports. Context is bounded. Prompt `repair-v5` asks for distinct competing mechanisms and distinguishes literal text probes from causal proof.

Six genuine NVIDIA planner-only runs at seed 47 are in `benchmarks/repair/backend-audit/hypotheses-v2/`; their manual rubric and report hashes are in `hypothesis-review.json`. All six returned three hypotheses. Five leading mechanisms match the fixture defects precisely; fraud is partial because it misstates the serialized module identity. Loan identifies the correct sparse/sparse_output change but gives an inaccurate removal version. Alternatives sometimes overlap or rely on unsupported possibilities. This is one seed on authored fixtures, with reference-aware review, and says nothing about full autonomous repair success.

The first six probes are preserved separately. They exposed missing fraud training context. Context was improved and the whole six-fixture probe repeated; unfavorable observations were not deleted.

## Backend checklist and evidence

| Items | Rechecked behavior | Qualification |
|---|---|---|
| BE-01/02 | Durable stages/checkpoints; immutable receipt kinds; cancel/fencing; tenant-scoped API and resumable events; concurrent payload-bound idempotent create | SQLite/Postgres race tests, HTTP retry test, simulated crash after receipt, busy heartbeat and durable scripted engine test. Not a new genuine UI inference run. |
| BE-03/04/05 | HTTPS/zip intake, tracked snapshot digest, bounds, contract builder/validation, sandboxed test discovery | Fast backend route/intake/tool tests; real browser source/contract checks. Private Git authentication is not supported by the public-source path. |
| BE-06/07/08/09 | Separate packs, isolated runner, verifier authority, PostgreSQL registration and holdout ledger | Snapshot bytes, sealed digest and dependency image bound at admission; stale/different diff cannot gain a second opening. Docker daemon remains trusted; legacy importing sealed tests fail closed. |
| BE-10/11 | Patch/replay/draft PR flow, explicit publication, no merge | Mocked GitHub protocol tests and real replay browser checks. Real GitHub external delivery not performed. |
| BE-12 | Invite/account gate, kill switch and atomic estimated global dollar/token commitments | SQLite/Postgres concurrent reservations; unknown usage retains ceilings. No invoice guarantee. |
| BE-13 | Official research, provenance and matched Tavily-off pair | Current-engine loan/B01 seed 47 D and D-TAV both independently VERIFIED. D actually used official docs. One fixture/seed, no population benefit claim. |
| BE-14/15 | Parallel independent candidate workspaces; ranked literal evidence probes | Tested scripted behavior. No information-gain implementation; wording alternative is explicit. |
| BE-16 | Label-free v4 drift profile feeds no-failing-test planner | Existing backend tests re-run; not a full production data observability system. |
| BE-17/18 | Mandatory pinned preparation/index policy, immutable image reuse, honest provider state/accounting | Exact top-level pins/wheels; transitive requirements not fully hashlocked. Missing usage never invents free calls. |
| BE-19 | Alembic v4 upgrade/downgrade and new admission tables; fresh PostgreSQL startup | Existing application superuser roles are not automatically migrated. |
| BE-20 | TTL and owner deletion; API active references, sandbox images/environments, verifier caches | **Partial: hard total disk quotas need host/runtime enforcement.** Admissions and holdout permits never expire. |
| BE-21/22 | Redaction/SSRF/path/injection/archive defenses, metrics/readiness/logs and busy heartbeat | Scoped automated tests, not external security certification or production alerting/SLO evidence. |
| BE-23/24 | Accounts/teams/roles and GitHub App webhook authorization/delivery logic | Automated API/provider mocks; real OAuth/App registration/delivery remain external checks. |
| BE-25 | Binary/multiclass/regression metrics and declared loaders | Tested supported schemas; arbitrary frameworks/loaders are not universally compatible. |

## Genuine Tavily pair provenance

`benchmarks/repair/backend-audit/tavily-pair-v2/` fixes one fixture, seed and model mapping for D/D-TAV, admits immutable images/contracts, and uses a separate HTTP verifier with PostgreSQL. D research is AVAILABLE and cites the official scikit-learn OneHotEncoder documentation; both independent verdicts are VERIFIED. This was native local NVIDIA inference, not Nebius or a Compose UI run. Unknown call accounting remains null in aggregate costs. The preceding `tavily-pair/` is retained: its D run incorrectly classified the compact library traceback as NOT_NEEDED. Recognizing known API symbols fixed that trigger before the v2 pair; successful repair in the first pair did not prove research activation.

The original partial 72-slot study remains frozen to b8d6f94/repair-v4. Current admission and prompt changes require a new study directory. The harness writes immutable configuration manifests and refuses mixed-version resumes. See `benchmarks/repair/LIVE_EVALUATION.md`.

## Operations verification

See [V5_OPERATIONS.md](V5_OPERATIONS.md) for reproducible commands and remaining host dependencies. Eight-service local readiness, least-privilege app roles, source-only API catalog, image preparation, checksummed quiesced backup and empty-target restore are audited independently of the existing user stack. Local image IDs must be retained/exported for off-host recovery. Fresh-host timing, remote CI, hosted TLS and real Nebius runtime checks are not inferred from a healthy laptop stack.

Final command outputs are under `benchmarks/repair/backend-audit/`. Dedicated temp directories and separate disposable PostgreSQL databases isolate tests; early overlapping test invocations encountered temp/schema cleanup interference and were rerun in isolation. Only final successful outputs should be used as acceptance evidence.

## Completed restore and image drill

`warm.txt` records all six fixture images and two preparations each: cached 1.42–2.24 seconds; first churn preparation 149.71 seconds. `backup-drill.txt` and `restore-drill.txt` show both databases and all required volumes restored into `codex-v5-restored`, with five nonempty embedded code/ML receipt hashes, whole replay bytes, DB sentinel and ledger admission/opening fingerprint identical. All eight restored services became healthy. This drill used a historical genuine replay, not newly executed tasks: relational task/receipt tables were empty and the DB marker/ledger entries were diagnostic. It proves the listed restored state, not a new live repair or off-host disaster recovery.

## Final acceptance results

- Fast backend, including SQLite/PostgreSQL: **340 passed, 1 skipped**.
- Real Docker boundary: **34 passed, 1 Windows-specific skip**.
- Slow fixture/checker/training suite: **23 passed**; separate durable engine-to-receipt test: **1 passed**.
- **398 unique passing backend tests**, two skips. Focused hardening/accounts rerun: 45 passed (overlaps the fast suite; do not add to the unique total).
- TypeScript check passed; real eight-service browser smoke: **2 passed**, one optional screenshot capture skipped. Production web/backend images built successfully.
- Backup/restore succeeded; overwrite and corrupted-dump guards refused unsafe restores. Restored diagnostic project stopped with data retained. Original user stack untouched; audited preview remains on localhost:3135/repair.
- Acceptance artifact hashes and limitations: `benchmarks/repair/backend-audit/acceptance.json`.

### PR description notes for frozen contracts

Neither `backend/app/repair/contracts.py` nor `backend/app/repair/sandbox/base.py` changed. A follow-up interface revision should declare separate immutable-image preparation and trusted artifact-evaluation capabilities (`environment_image`, `prepare_pinned`, `trusted_run`), with explicit protocol/version negotiation and distinct credentials. Current adapters expose optional platform methods outside the frozen base protocol and fail closed when a required production capability is absent. This compatibility proposal is not silently presented as a change to the agreed interfaces.
