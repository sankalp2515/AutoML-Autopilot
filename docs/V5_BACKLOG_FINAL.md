# AutoML Autopilot v5 — final backlog to a shippable product

Updated 30 Oct 2026 after [release PR #1](https://github.com/sankalp2515/AutoML-Autopilot/pull/1) merged into `main`. Scope: v5 repository repair, hosted judge access and the stated production acceptance criteria. Nebius Token Factory and Sandboxes are now integrated and genuinely exercised. Remaining evidence/production limitations stay explicit.

**Priority**
- **P0:** required to ship and to demo.
- **P1:** required to be a strong grand-prize entry and a trustworthy product.
- **P2:** after launch.

**Status**
- **Done / ✅ Merged:** implemented in `main`, with the scoped validation and limitations stated in each row.
- **Branch:** retained only in historical notes; all v5 implementation/card branches and the separate evaluation branch were consolidated by release PR #1.
- **Open:** not started.
- **◐ Partial:** implementation exists but a stated acceptance criterion or evidence dependency remains outstanding.

Latest integration: [API handoff validation](CODEX_API_HANDOFF_VALIDATION.md).

Latest acceptance and live-run evidence: [frontend / fixture completion](FRONTEND_FIXTURES_EVIDENCE_COMPLETION.md).
Frontend implementation is merged into `main`. Two evidence dependencies remain: EV-02 leakage recording and EV-04 completed comparison; neither is a missing UI component.

See [Codex completion audit](CODEX_V5_COMPLETION_AUDIT.md) for evidence and launch limits.

## 0. Where we are

Release PR #1 consolidates `v5/main`, C1–C4, the oracle/agent/backend/frontend branches, Codex hardening/deployment work and the separate product-path evaluation branch. It merged as `6553657`; the final documentation follow-up is recorded in Git history. The original v4 implementation remains in the ancestry and its documented secondary mode remains available. All seven GitHub CI jobs passed on the reviewed release head ([CI run](https://github.com/sankalp2515/AutoML-Autopilot/actions/runs/41092136984)).

Historical replays retain their original unisolated runner disclosures. Production
worker construction now requires isolated execution and a verifier service.
Test-only process execution remains for deterministic checker proofs.

---

## 1. Known bugs and gaps (fix first)

| ID | P | Problem | Done when | Status / evidence |
|---|---|---|---|---|
| BUG-01 | P0 | Live runs use the **unisolated process runner** | Agent, checkers and scripts use the C2 Docker runner by default; the process runner is only allowed in tests | ✅ Merged — Docker/service default; process factory blocked outside tests; CLI uses durable API. |
| BUG-02 | P0 | C2 and C4 are not merged; their PR-description files (`docs/C2_PR.md`, `docs/C4_PR.md`) are in the branches | Merged into `v5/main` with tests green; PR files removed | ✅ Done in v5/main — b8faa60 / 8c58aeb merged C2/C4; PR-description files absent. |
| BUG-03 | P0 | The agent's file tools need a **host work directory**, which breaks Token Factory Sandboxes and a separate sandbox service | Tools read, list, search and edit through runner calls; no host path needed | ✅ Merged — file cache refreshed through runner calls before actions; edits sync via write_files; no sandbox host directory. |
| BUG-04 | P0 | The coding token budget is shared per task, so the 2nd candidate starved (live run 2) | Per-candidate budgets; candidates can run in parallel | ✅ Merged — independent candidate-scoped token allowances, shared audit ledger, parallel branches; one-call prompt-token overshoot possible. |
| BUG-05 | P0 | Sealed tests and the parity probe run **in the same process** as candidate code | Grader probes run in a separate container from the same candidate image; the candidate process can't touch them | ✅ Merged — supported sealed artifact tests run in a clean independent container using the admitted dependency image; candidate source/plugins/verdict text are absent. Real Docker adversarial tests pass. Legacy candidate-import sealed pytest remains unsupported and fails closed; see V5_OPERATIONS.md. |
| BUG-06 | P0 | Checkers run inside the agent process, not the verifier service | Verifier service owns the sealed bundles and labels and drives the sandbox; the worker sends only the diff and contract digest | ✅ Merged — production worker refuses local oracle fallback; verifier service owns grading. LocalOracles remains a test helper. |
| BUG-07 | P1 | `workdir_diff` silently ignores files with uncommon extensions | Unknown or binary files in edit roots are listed and refused visibly | ✅ Merged — unknown/binary/invalid-UTF8 changes surfaced and refused. |
| BUG-08 | P1 | `run_repair.py` uses a fake all-zero commit SHA | Every run goes through intake with a real pinned SHA and snapshot digest | ✅ Merged — CLI uses API intake; proofs record real SHA and snapshot digest. |
| BUG-09 | P1 | Hypothesis quality is weak (live hypotheses blamed the scaler or the target) | The planner sees the failing test, a traceback and the key files it references; quality measured on the fixtures | ✅ Merged — failing tests, traceback, train/predict entrypoints and imports supplied. Six genuine NVIDIA planner runs repeated under repair-v5: five precise primary mechanisms, one partial; one version-detail error. Single-seed manual rubric in backend-audit/hypothesis-review.json; not a claim of perfect hypotheses. |
| BUG-10 | P1 | The diagram claims "cheap discriminating experiments", but probes are free-form | Either rank probes with the v4 information-gain-per-cost scorer, or change the wording | ✅ Merged — README separates v5 proposed checks from v4 information-gain ranking. |
| BUG-11 | P1 | Repair tests take ~11 min (the model is retrained per environment) | Slow tests marked; shared trained-artifact cache; CI split into fast and slow jobs | ✅ Merged — slow markers/CI split; bounded opt-in test artifact cache; deterministic single-thread BLAS and no unrelated host pytest plugins. No live cache or measured speedup claim. |
| BUG-12 | P1 | NVIDIA catalog: Super and Nano 3 return 404, so Ultra does the coding (about 3× cost); Nebius model ids unverified | Model ids confirmed per provider; tier mapping tested | ◐ Partial — NVIDIA Ultra successful calls recorded; Super/Nano return 404. Later Ultra probes on all four independent credential slots returned 503. Offline tier/404 tests pass; Nebius account execution awaits owner key. |
| BUG-13 | P1 | The finish gate recognises tests only by a `test_` filename prefix | Uses pytest's own test discovery | ✅ Merged — finish uses pytest collection and custom patterns; JUnit respects collected file paths. |
| BUG-14 | P2 | The agent's fixes can be correct but brittle (it reordered a hard-coded list) | Reviewer-facing "maintainability notes" in the receipt; prompt prefers a single source of truth | ✅ Merged — report includes static reviewer notes for repeated literal lists and broad exceptions; shared-source prompt merged upstream. Notes are not a quality verdict. |

---

## 2. Backend: repair engine and services

October 30 re-audit: see [BACKEND_OPERATIONS_AUDIT.md](BACKEND_OPERATIONS_AUDIT.md) and [V5_OPERATIONS.md](V5_OPERATIONS.md). Checkmarks mean the scoped implementation and listed tests, with explicit external/production limits; they do not mean every hosting acceptance gate passed.

| ID | P | Component | Done when | Status |
|---|---|---|---|---|
| BE-01 | P0 | **Durable repair stages in the worker** | The agent runs as persisted stages (reproduce, hypotheses, candidates, screen, finalist, verdict). Events, candidates and receipts are written as they happen. Cancel works. A crashed run ends FAILED and is never half-verified. | ✅ Merged — stage events, candidates, code/screen/final receipts checkpoint during execution; simulated crash preserves evidence and ends FAILED. Busy-run heartbeat and stale fencing tested. |
| BE-02 | P0 | **Task API v5** (create, get, list, cancel, events, candidates, diff, receipt) | Tenant-scoped; idempotent create; resumable event stream; fencing on writes | ✅ Merged — payload-bound tenant Idempotency-Key, concurrent HTTP retry and SQLite/Postgres CAS/event/tenant tests pass; UI reuses key after failed request. |
| BE-03 | P0 | **Repository intake API** | Public GitHub/GitLab HTTPS URL + branch or SHA, or a zip upload. Size and file-count limits. Clone into a snapshot with a digest. | ✅ Done: URL+ref or zip; size/file limits; snapshot digest (merged; Claude implementation, integration audited here). |
| BE-04 | P0 | **Test discovery** | `pytest --collect-only` in the sandbox; returns test ids and the currently failing tests, so users pick the failing test instead of typing ids | ✅ Done (merged; Claude implementation, integration audited here). |
| BE-05 | P0 | **Contract detection and builder API** | Reads `autopilot.yaml` if present. Otherwise proposes one (commands, data paths, entrypoints, edit roots) from the repo layout. The validate endpoint returns readable errors. | ✅ Done (merged; Claude implementation, integration audited here). |
| BE-06 | P0 | **User evaluation pack** (what makes it usable by anyone) | The user uploads holdout labels (and optional segment column) separately from the repo. Labels stay on the verifier side. Without labels, only label-free ML checks run (parity, availability), and the verdict says so. | ✅ Done: label packs; LIMITED_NO_LABELS verdict without them (merged; Claude implementation, integration audited here). |
| BE-07 | P0 | **sandbox-runner service** | The only component that starts candidate containers. No provider keys, no labels. The worker and verifier call it over the internal network. Docker-socket risk documented (prefer rootless Docker or gVisor). | ✅ Local Docker + hosted Nebius service — distinct trusted evaluator capability, ownership, restart orphan cleanup, bounded concurrency/environments and archive/output limits. Hosted adapter owns the sandbox control credential (architecture exception to the original no-provider-key gateway requirement); guest code receives none and gateway has no Docker socket. Host Docker daemon remains trusted for local mode. |
| BE-08 | P0 | **Verifier service v5** | Hosts the code and ML checkers, sealed bundles, user evaluation packs and the holdout ledger; calls sandbox-runner (fixes BUG-05/06) | ✅ Merged — verifier-only bundles/packs and separate PostgreSQL ledger; registration token distinct from worker grading token; immutable snapshot/image/sealed-bundle binding. Legacy sealed Python compatibility limitation documented. |
| BE-09 | P0 | **Holdout ledger and admission records in Postgres** | One holdout opening per task, durable and transactional; stored contract digest checked at grading (W04/W05) | ✅ Merged — verifier-only PostgreSQL admissions and diff-bound single finalist permit; independent verifier instances, concurrent opening and restart tested on SQLite/Postgres. Permits never expire. |
| BE-10 | P0 | **Delivery** | `.patch` download; GitHub draft PR from the user's token or a GitHub App, with the receipt in the PR body; never merges | ✅ Done (merged; Claude implementation, integration audited here). |
| BE-11 | P0 | **Replay recorder and player API** | Every genuine run can be published as a read-only replay (events and artifacts); public demos stream replays at zero cost | ✅ Done (merged; Claude implementation, integration audited here). |
| BE-12 | P0 | **Live-run access control and spend caps** | Invite codes or sign-in for live runs; daily token and dollar caps with atomic reservations; a kill switch; anonymous users get replays and sample-repo runs within caps | ✅ Merged — authenticated/invite custom live access; anonymous samples, kill switch and atomic global estimated dollar/token reservations. Concurrent admission tested SQLite/Postgres. Unknown usage retains reservation; judge-only mode additionally enforces an invite for signed-in users, cumulative estimated allowance, cooldown and shared active slots. These are estimated commitments, not invoice guarantees. |
| BE-13 | P1 | **Tavily inside the loop** | When a traceback names a library or packages changed, search official docs and give the agent cited excerpts as data. Shown in the UI with provenance. Tested on B01 with a Tavily-off comparison. | ✅ Merged — genuine B01 seed-47 matched D/D-TAV pair both independently VERIFIED; D retrieved official scikit-learn documentation, disabled arm did not use Tavily. Separate current-engine artifacts; one pair cannot establish general benefit. |
| BE-14 | P1 | **Parallel candidates** | Candidates run concurrently, each with its own budget; total wall time drops | ✅ Done (merged; Claude implementation, integration audited here). |
| BE-15 | P1 | **Ranked probes** (or BUG-10 wording) | Hypotheses carry predicted outcomes; the information-gain scorer picks the next probe; results update beliefs | ✅ Wording alternative — source-text probes rank matching/contradicting evidence. No information-gain scorer or causal confirmation claim; BUG-10 alternative retained. |
| BE-16 | P1 | **Fold the v4 data investigator in** | For "metric dropped" tasks without a failing test, the agent first runs the v4 investigation on data windows | ✅ Done: the v4 drift profiler (PSI, missing-rate shift) feeds the planner when there is no failing test (merged; Claude implementation, integration audited here). |
| BE-17 | P1 | **Dependency install policy** | Installs happen only in `prepare`, from pinned requirements, through an allow-listed package index; image digest recorded in the contract | ✅ Merged — mandatory exact top-level pins, operator HTTPS index, wheels only, minimal build context and immutable image bound into ECC. Trusted preparation never repeated during pinned execution. Transitive hash-locking remains outside this policy. |
| BE-18 | P1 | **Provider status v5** | Shows configured, last successful call, model per tier and fallback, never "live" just because a key is present | ✅ Merged — worker-reported provider/tier state and timestamp of successful calls. Missing usage remains unknown and stops further tier calls; configured keys never presented as successful inference. |
| BE-19 | P1 | **Database migrations** for v5 tables (Alembic) | Upgrade from the v4 schema tested | ✅ Merged — Alembic 0003/0004, fresh Compose PostgreSQL initialization and v4 upgrade/downgrade tests. Existing superuser roles require owner migration; no automatic alteration of existing stack. |
| BE-20 | P1 | **Cleanup and retention** | Time-to-live for clones, environments, images, uploads and evaluation packs; owner delete; disk quotas | ◐ Branch — API TTL/owner delete, active-reference protection; sandbox-owned environment/image TTL, verifier cache TTL and environment/concurrency limits. Hosted data now has an aggregate 20-GiB filesystem limit; guest writable layers 2 GiB. Per-tenant quotas and immediate removal of provider-retained images remain unsupported. See NEBIUS_DEPLOYMENT_VALIDATION.md. |
| BE-21 | P1 | **Security pass** | Secret redaction in prompts and logs; prompt-injection exercise (B14); path-escape tests; SSRF guard on repo URLs; outbound allow-list | ✅ Scoped tests — secret redaction, B14 injection, path/SSRF/archive escapes, Git redirects/protocol restrictions, credential segregation and prepared package-index policy. Host Docker/socket and dependency supply chain remain trust assumptions; not an independent penetration-test certification. |
| BE-22 | P1 | **Observability** | Structured logs per task; metrics (queue age, run time, tokens, cost); readiness that checks worker, verifier and sandbox-runner | ✅ Merged — task logs, queue/runtime/token metrics and honest unknown-cost accounting; worker heartbeat while busy, readiness checks all execution dependencies. Hosted HTTPS readiness and gross-cost Google email alert routing configured; no production SLO guarantee. |
| BE-23 | P2 | Accounts and teams | GitHub sign-in, workspaces and roles | ✅ Backend merged in v5/main (be1979c); frontend connected on this branch. External OAuth remains untested. |
| BE-24 | P2 | GitHub App with webhook | Failed CI creates a repair; verified repair may open a draft PR | ✅ Backend merged in v5/main (be1979c); links/status/recent outcomes connected. App registration and real delivery remain external checks. |
| BE-25 | P2 | Multiclass, regression, custom model loaders | Compatible metrics, prediction schemas and parity | ✅ Backend merged in v5/main (be1979c); guided problem/metric/loader fields and relative-error display connected. Arbitrary-framework compatibility is not established. |

---

## 3. Frontend: v5 product UI — implementation and remaining dependencies

The v5 frontend is implemented and merged into `main`. Its information architecture is: **Home → New repair → Live workspace → Candidates → Verdict / PR → Runs → Benchmark → Docs**. v4 becomes a secondary mode ("Investigate a data incident").

| ID | P | Screen / section | Must contain | Status / limitations |
|---|---|---|---|---|
| FE-01 | P0 | **App shell and navigation** | v5 name and tagline ("The ML coding agent that doesn't stop at green tests"); nav: Home, New repair, Runs, Benchmark, How it works; legacy pages removed from navigation; provider/runner status pill | ✅ Merged — shell/navigation/status/themes. |
| FE-02 | P0 | **Home / landing** (the judges' first screen) | Hero with one-line promise; three entry points: **Watch a verified repair** (replay, instant), **Try a sample repo** (live, capped), **Fix your repo**; a measured headline from the benchmark (false-accept rate of "tests pass" agents); a 5-step "how it works" strip; trust strip (sandboxed, sealed holdout, never merges) | ✅ Merged — complete public landing (hero, interactive evidence, fixture stories, workflow, benefits, $0 judge access, limits, FAQs and CTA); responsive light/dark and source/license. Five checker proofs explicitly labelled authored. Generic-agent headline is gated on a complete EV-04 study; current partial study is not published as a headline. |
| FE-03 | P0 | **Replay player** | Plays a recorded genuine run with timeline scrubbing, speed control and a guided tour (reuse the v4 tour) that stops at hypothesis, tool call, green tests, ML rejection, verified finalist; labelled "recorded run" | ✅ UI / ◐ EV-02 — playback, tour and genuine recordings complete; new isolated NVIDIA churn and loan replays bundled. Specific genuine leakage-rejection recording remains outstanding. |
| FE-04 | P0 | **New repair: source step** | Sample repository gallery (cards per fixture with the bug story); GitHub URL + branch/SHA; zip upload; size limits; privacy notice | ✅ Merged — source flow/catalog automatically finds all six repositories. |
| FE-05 | P0 | **New repair: contract step** | Detected `autopilot.yaml` shown and validated, or a **guided contract builder** form (commands, data paths, serve entrypoint, editable folders, ML invariants) with live YAML preview and inline errors | ✅ Merged — raw YAML + guided example builder; examples require repository-specific review. |
| FE-06 | P0 | **New repair: task step** | Failing-test picker from test discovery; task description; optional evaluation pack upload (holdout labels, segment column); mode and budget (Advanced: max candidates, token cap); cost estimate; Start | ✅ Merged — discovery/task/pack/mode complete; Advanced candidate/token limits, debounced server estimate, API validation and immutable admission binding. Budgets can only reduce server ceilings; estimates are not invoice caps. |
| FE-07 | P0 | **Live run workspace** (the hero demo screen) | Stage timeline; **agent activity stream** (thought → tool call → result, denials highlighted in amber); **hypothesis board** (competing causes and their status); **candidate tabs** with live diff and terminal tail; budget meters (tokens per tier, estimated $, tool calls, elapsed time); cancel; reconnecting state | ✅ Merged — persisted incremental diff/tool/usage events drive candidate tabs, terminal, tier meters and elapsed/tool counts before completion; previews capped at 64 KB. Reconnect/cancel preserved. |
| FE-08 | P0 | **Candidate comparison** | Side-by-side cards per candidate: diff, visible tests (fail-to-pass, pass-to-pass), code-checker checks, ML-screen checks, eligibility. A clear **"Tests green — rejected by ML check"** state with the reason in plain words ("online scores differ from batch on 200/200 rows") | ✅ Merged — candidate comparison and green-but-rejected evidence. |
| FE-09 | P0 | **Verdict and receipt** | Decision banner and plain-language summary; finalist diff viewer (syntax-highlighted, split/unified); regression test the agent added; both receipts with every check; holdout metric with CI; provenance badges; cost per tier; reproduce command; download `.patch` and receipt JSON; share link; print | ✅ Merged — receipt/diff/download/share/print; CI is paired metric change. |
| FE-10 | P0 | **PR handoff** | PR preview (title, body, receipt); connect GitHub / paste token; create draft PR; link to the created PR; clear note that nothing merges automatically | ✅ Merged — draft PR integration; real external write not performed. |
| FE-11 | P0 | **Runs list** | Your runs with repo, task, decision, cost, duration, filters; replays marked | ✅ Merged — scoped history/filters and published/bundled replays. |
| FE-12 | P0 | **States everywhere** | Loading, empty, error, queued, budget-exhausted, provider-down (falls back to replay), sandbox-down; honest copy | ✅ Merged — honest states/replay recovery; no invented live evidence. |
| FE-13 | P1 | **Benchmark v5 page** | Arms table (generic agent vs full vs no-ML-check vs no-Tavily); false-accept headline chart; per-fixture matrix; cost per verified fix; failures listed; v4 incident benchmark as a secondary tab | ✅ UI / ◐ EV-04 — checker matrix, pinned public task proofs, measured-arm table/chart and failure/cost states implemented. Complete study required before publishing comparative headline; Historical comparison is incomplete; current-engine runs must use a separate study directory. |
| FE-14 | P1 | **How it works / trust model** | Architecture (as-built vs target), trust zones, what the agent can and cannot do, what "VERIFIED" means and doesn't | ✅ Merged — runtime versus target trust model and VERIFIED limits. |
| FE-15 | P1 | **Docs pages** | `autopilot.yaml` reference with examples; supported repos and limits; evaluation pack format; privacy and data retention; FAQ | ✅ Merged — docs/reference/pack/privacy/FAQ; deployment retention requires confirmation. |
| FE-16 | P1 | **Data incident mode** (v4) | Reachable from New repair as "My model's metric dropped (no failing test)"; reuses the existing v4 screens | ✅ Merged — existing incident workflow reachable. |
| FE-17 | P1 | **Diff viewer component** | Reusable, accessible, handles new/deleted files, word-level highlights, copy | ✅ Merged — accessible split/unified text diff/copy/highlights. |
| FE-18 | P1 | **Accessibility and responsive pass** | Keyboard and screen reader on all v5 screens; workspace usable at 1280 px and readable on mobile (read-only on phone is acceptable); both themes | ✅ Merged — light/dark desktop/mobile/keyboard/axe; no full assistive-tech user study. |
| FE-19 | P1 | **Frontend tests** | Playwright on recorded runs for every P0 screen; axe checks; one live-stack smoke test | ✅ Merged — 24 offline browser cases, 38 v5 axe audits, and five final real-API browser checks including a durable repair with edited limits; CI includes v5. Scripted UI inference is separate from NVIDIA evidence. |
| FE-20 | P2 | Sign-in, workspaces, team runs | | ✅ Merged — GitHub callback, account/workspace headers, workspace creation/switching, member roles and CI links wired. Seeded real-API tests; external OAuth/App installation not exercised. |

---

## 4. Fixtures, evaluation and evidence

| ID | P | Item | Done when | Status / evidence |
|---|---|---|---|---|
| EV-01 | P0 | Fixtures B01, B03, B06, B09, B14 with sealed manifests and proofs | Each proof shows unpatched fails, wrong fix green-but-rejected, reference passes; in CI | ✅ Merged — all five three-way proofs in CI, plus B01 dummy-column misalignment/unseen category and B09 artifact identity/no-training-rows/repeatability/fresh-online protocols. Authored checker evidence, not autonomous success claims. |
| EV-02 | P0 | Recorded replays of genuine runs (at least: churn REJECTED-both, churn VERIFIED, one leakage rejection) | Playable in FE-03 | ◐ Evidence blocked — two historical genuine churn recordings, isolated NVIDIA churn/loan VERIFIED, and genuine hosted Nebius churn/loan VERIFIED plus refund code rejection. A sealed leakage rejection remains missing; the separate product-path study also stopped before ML leakage grading. See benchmarks/repair/api-eval-2026-10-30/REPORT.md. No fabricated replay. |
| EV-03 | P1 | Comparison harness: arm A (generic "tests pass" agent), D (full), D-ML−, D-TAV− | Same checkers grade every arm; fixed seeds; report with denominators and losses | ✅ Merged — executable four-arm harness, fixed seeds, same admitted limits/checkers, independent grading after no-ML/generic acceptance, resumable slots, hashes, denominators, Wilson intervals, missing-cost handling; tested. Results are EV-04. |
| EV-04 | P1 | Live evaluation runs and report | Headline numbers on the Benchmark page come from this file only | ◐ Incomplete studies — historical six pilots and 15/72 four-arm slots retained. Separate product-path study (api-eval-2026-10-30/REPORT.md): 8/12 slots completed (A: 2 VERIFIED/1 REJECTED/1 INCONCLUSIVE; D: 1/2/1). HTTP 429 affected 5/8 slots and biases the parallel arm; no arm ranking or comparative headline. Hosted Nebius UI validation is separate integration evidence, not completion of either study. |
| EV-05 | P1 | One or two fixtures modelled on real open-source ML repo patterns | Shows the product isn't tuned to toy repos | ✅ Merged — two pinned public MLOps-Bench repository patterns: Great Expectations serving and MLflow features. Unchanged public tests: originals 0/3 new behavior and 4/4 preserved; references 7/7. Not independent production-repo or autonomous pilots. |
| EV-06 | P2 | A small MLOps-Bench task subset with its own harness | No leaderboard claims unless the harness is reproduced exactly | ✅ Merged — two-task pinned MLOps-Bench subset, source hashes/licenses, problem+repo agent export and separate public evaluator harness; proof artifact and CI. No full-suite/hidden-test/leaderboard claim. |

---

## 5. Infrastructure and operations

| ID | P | Item | Done when | Status / limitations |
|---|---|---|---|---|
| IN-01 | P0 | Compose v5 | Segmented services, credential separation, health checks | ✅ Merged — eight services including separate verifier PostgreSQL; all healthy, readiness true, non-superuser app roles. Existing stack untouched. |
| IN-02 | P0 | Candidate base/fixture images | Cached prepare in seconds; digests recorded | ✅ Local Docker — Python 3.11/3.12 and six fixture images warmed; cached prepares 1.42–2.24 s, immutable IDs saved in backend-audit/warm.txt. First churn preparation 149.71 s; downloads remain cold-start work. |
| IN-03 | P0 | CI | Fast/slow/Postgres/Docker/frontend/image checks green on merge | ✅ GitHub CI — all seven jobs passed on release PR #1: fixtures, fast backend, PostgreSQL, slow checker/scripted-agent proofs, real Docker boundary, frontend type/build/browser checks and image builds. Current main results are linked by the README badge. |
| IN-04 | P0 | One-command local start | Samples/replays without keys; clean setup under ten minutes | ◐ Branch — scripts/v5_stack.py up builds/migrates/starts healthy eight-service local stack without .env or model calls. README instructions added. Fresh-machine under-ten-minute timing not established. |
| IN-05 | P1 | Hosting target | Domain/TLS/backups/restart; keyless replay | ✅ Hosted — Google Cloud CPU VM, temporary public HTTPS hostname, Caddy TLS, reboot startup, application backup timers, daily off-host disk snapshots and email alerts. See NEBIUS_DEPLOYMENT_VALIDATION.md; ongoing account/credit/uptime operation remains owner responsibility. |
| IN-06 | P1 | Backup and restore drill | Restore identical receipt digests | ✅ Local + cloud drills — cloud backup restored five genuine tasks and 13 persisted receipts into a distinct loopback-only project; receipt hashes and verifier-ledger fingerprint identical, no inference. Private cloud-state checksums validated. Earlier local replay-byte/sentinel drill retained. Daily off-host disk snapshots configured; provider image export/retention is a separate limitation. Evidence: benchmarks/repair/nebius-cloud-restore.json. |
| IN-07 | P1 | Secrets handling | External state, rotation notes; no credential artifacts | ✅ Merged — generated internal credentials outside checkout, model credentials in the repair worker and sandbox control credential in the hosted gateway; no guest credentials, role/token separation and rotation guidance. Exact-value scan passed; Windows operator state/backup ACLs remain host responsibility. |
| IN-08 | — | Nebius switch | Real Token Factory/runtime fixture validation | ✅ Integration — direct TokenFactoryRunner implemented; genuine Super/Nano calls and sandbox boundary probes passed. Public UI churn, loan and fraud VERIFIED; refund REJECTED (code failure); credit INCONCLUSIVE (invalid model actions). Four distinct fixtures reached correct terminal verdicts through the UI; credit reliability failure preserved. Evidence: benchmarks/repair/nebius-ui-validation.json. |

---

## 6. Repository, docs and submission

| ID | P | Item | Status / evidence |
|---|---|---|---|
| DOC-01 | P0 | README rewritten for v5: problem, 2-minute replay path, local start, `autopilot.yaml`, results, limits, how Nemotron and Tavily are used | ✅ Merged — v5-first README, two-minute recording path, local start, contract example, evidence/limits, Nemotron/Tavily roles, license and legacy links. Local startup/browser checks; hosted and fresh-machine guarantees excluded. |
| DOC-02 | P0 | As-built architecture diagram (same layout as the target diagram, coloured built / in progress / roadmap) | ✅ Merged — README embeds the exact supplied docs/assets/production-architecture.png; docs/V5_ARCHITECTURE.md and docs/assets/v5-architecture.svg distinguish the as-built inventory, same developer/source/control/delivery/infrastructure layout; built, integration-pending and roadmap colors. Updated for the deployed Google Cloud + Nebius adapter topology; provider retention and remaining external checks explicit. |
| DOC-03 | P0 | Merge `v5/main` into `main` when the ship gate passes; v4 remains reachable as a mode | ✅ Merged — release PR #1 integrated all v5 work into main (6553657), including the separate evaluation branch; earlier v4 history/mode preserved. |
| DOC-04 | P0 | Demo video script and recording (3 min) | Owner scope — video not produced in this task. |
| DOC-05 | P0 | "Significantly updated" statement, Token Factory feedback, license visible | ✅ License portion — GPL-3.0 preserved in root LICENSE and linked visibly in README/landing footer; upstream notices retained. ✅ Significant-update and genuine Token Factory feedback drafts now in SUBMISSION_TECHNOLOGY_NOTES.md; owner must enter the final submission answers. No feedback message sent externally. |
| DOC-06 | P1 | Legacy orchestrator code: keep, move or document (owner decision) | ✅ Documentation decision — retain original orchestrator and v4 in place for reproducibility; docs/LEGACY_SCOPE.md distinguishes primary/secondary/unsupported scope. Destructive cleanup/extraction and relicensing remain owner decisions. |
| DOC-07 | P1 | CONTRIBUTING / support / issue templates for real users | ✅ Merged — CONTRIBUTING.md, SUPPORT.md, SECURITY.md, bug/feature issue forms and PR template; private judge-access recovery and sanitized evidence guidance. Published on GitHub main; README rendering/images and documentation links checked. |

---

## 7. Ship gate (when `v5/main` becomes the product)

1. BUG-01…06 fixed: isolated runner by default, verifier service owns grading.
2. A live run on at least 4 fixtures through the UI (not scripts), each ending in a correct verdict.
3. A new user can go from Home to a verified repair (or an honest rejection) on a sample repo without help.
4. Replays work with no model keys.
5. CI green; README local start verified on a clean machine.
