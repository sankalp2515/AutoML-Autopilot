# C4: durable repair API and fenced stub worker

Repair tasks, candidates, attempts, receipts and events are persisted in new
`repair_*` tables. Worker writes compare the task version, monotonically
increasing fencing token, lease owner and lease expiry in the database. The task
update, child record and event append use one transaction. Cancellation performs
its own version/token comparison and revokes the worker's fence. Existing
candidate and receipt records cannot be overwritten.

`app.main` includes the repair router; this is the only existing application file
changed. Shared contracts, sandbox interfaces, agent prompts and oracle files
are unchanged. No model, research, cloning or candidate execution is performed.

## API

- `POST /api/v1/v5/tasks`: validated repository contract, exact base commit SHA,
  snapshot digest and target. The platform assigns the task ID and seals an ECC.
- `GET /api/v1/v5/tasks`: tenant-scoped bounded list with limit/offset.
- `GET /api/v1/v5/tasks/{id}`: status, version, fencing token and contract digest.
- `GET /api/v1/v5/tasks/{id}/events?after=seq`: bounded resumable JSON event log.
- `GET /api/v1/v5/tasks/{id}/candidates`.
- `GET /api/v1/v5/tasks/{id}/candidates/{cid}/diff` and `/receipt`.
- `POST /api/v1/v5/tasks/{id}/cancel`: optional expected `version`; 409 if stale.
- Collection aliases `/events?task_id=...`, `/candidates?task_id=...`,
  `/candidates/{cid}/diff` and `/candidates/{cid}/receipt` retain tenant scoping.

Auth/session identity reuses the Autopilot tenant dependency. Cross-tenant and
wrong-task candidate requests return 404. There is no HTTP endpoint to change
task status or submit purported verification receipts. Internal receipt storage
validates candidate/contract/diff identity, oracle receipt digests and combined
decisions against the stored code/ML receipts; this does not authenticate an
oracle service identity by itself.

## Stub execution

Run `python -m app.repair.service` from `backend/` after configuring the database
through process environment variables. The worker polls the durable queue,
claims a lease, advances CREATED -> SNAPSHOTTING -> MAPPING and releases it.
Restarted workers can reclaim expired CREATED/SNAPSHOTTING work. MAPPING awaits
the future agent integration and is not repeatedly dispatched. Stub events carry
`stub: true, executed: false`; they are not evidence that a repository was cloned
or mapped. The API itself queues work without starting ephemeral background jobs.

## Validation

Final focused result: **34 passed** across SQLite and a separately created
disposable Postgres database. Tests cover
database using `REPAIR_TEST_POSTGRES_URL`. They exercise stale-worker writes to
status/candidates/attempts/receipts/lease renewal/release, concurrent claims and
effects, expiry without replacement, cancellation fencing/idempotence, lifecycle
checks, immutable receipts, missing ML evidence, cursor replay and tenant scope.

The unchanged shared-base frontend passed TypeScript and a production build.
Google Font responses were mocked through Next's test hook so the build made no
font-service calls. A cross-worktree C2+C3 smoke also passed: pinned local intake,
isolated failing execution, authorized edit, passing execution and external Git
diff, with the pristine snapshot and fork parent preserved.

The broad backend attempt reported **1089 passed, 28 skipped, 7 failed**. Five
legacy pipeline-graph failures needed Redis, and one Windows multiprocessing
failure was caused by launching pytest through stdin. Those six tests passed on
a targeted rerun using an isolated Redis instance and a normal pytest module
launcher, with external network blocked. The remaining failure,
`tests/test_agents/test_winner_direction.py::test_template_mirrors_the_same_rule`,
was reproduced independently on the unchanged shared base in wt-c3. Neither its
template nor its test is changed by these cards. Thus the full suite is not
reported as green. Final C4-focused validation (including the last additional
cases) remains 34 passed on SQLite/Postgres.

Run `python -m pytest tests/test_repair/test_service.py tests/test_repair/test_routes.py -q`.
Postgres cases skip unless `REPAIR_TEST_POSTGRES_URL` is explicitly provided.
Never point that variable at an existing application database: the tests create
and drop repair tables. Broader validation results are reported with the card.

## Interface proposals and integration limits

- Add a generic `receipt.recorded`/state-transition event in a reviewed contract
  revision. The frozen event vocabulary cannot accurately describe every
  INCONCLUSIVE receipt, so those persist without inventing a 'passed' event.
- Put the lease/fence mutation context and task-create request in versioned
  shared interfaces before additional engines and clients depend on them.
- Real intake must bind the API's supplied snapshot identity to the trusted
  admission record. This stub validates format; it does not prove source bytes.
- Oracle authentication, final holdout authority, migrations, durable execution
  beyond MAPPING, aggregate quotas and production admission remain their own
  cards. The existing terminal REVIEW_READY -> PR_DRAFTED exception is preserved.
- No push or remote PR/CI operation is performed by this card.
