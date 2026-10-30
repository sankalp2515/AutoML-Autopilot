# Codex API handoff integration — 30 October 2026

Branch: `codex/fixtures-bug-hardening`. Integrates `v5/main` through `430b68a`,
including Claude's `be1979c` and the earlier `f872221` agent changes. No push or
main-branch commit. The attached progress report is context; its “all done” labels
are checked against the implementation and exact acceptance criteria.

## Connected frontend

| Flow | Implementation and boundary |
| --- | --- |
| Identity | Anonymous session plus `X-Autopilot-Token` and `X-Workspace`. Expired sessions show a sign-in recovery message. Legacy v4 Supabase auth remains separate. |
| GitHub sign-in | `/auth/callback`, browser-bound state check, one callback exchange, query removal, session token in sessionStorage. Unconfigured sign-in is explained; no dead OAuth redirect. Real GitHub exchange was not performed. |
| Workspaces | `/me`, create/select workspace, full navigation resets mounted tenant state, current workspace in shell. |
| Members | List/add/change/remove by GitHub login. Viewer/member/admin/owner controls reflect role; server retains authority and last-owner protection. |
| CI repository links | Add/remove, installation confirmation, auto-draft preference, recent outcomes and task links. No installation URL is invented when the server has none. |
| Contracts | Guided binary/multiclass/regression choice, compatible metrics, custom model loader, existing YAML/errors/needs-input/discovery flows retained. |
| Investigation | Probe confirmed/refuted/unrunnable state, hypothesis reorder events, drift findings. Literal probe evidence does not establish a root cause. Replay views retain stage visibility. |
| Receipts | Relative RMSE/MAE improvement and confidence interval as percentages; positive always better; group quality table and insufficient-group caveat; image digest; automatic draft PR result/error; no-label limitation prominent. |
| Operations | Status/readiness retained; `/metrics` activity displayed as deployment totals, not benchmark performance. |

## Additional remaining item finished

**BUG-14:** the persisted report/receipt JSON now includes `maintainability_notes`.
Static AST inspection flags repeated literal lists and broad exception handlers,
and prompts review of the regression test/shared feature path. It executes no
candidate code, spends no model tokens, does not modify frozen receipt contracts,
and does not influence VERIFIED. The upstream prompt already prefers one shared
source of truth. The UI calls these review notes, not a maintainability certificate.

## Integration resolutions

Both branches changed the same verifier and agent paths. The combined version retains:

- Actual pytest collection, source-cache refresh, independent candidate budgets,
  unsupported-file rejection, test artifact cache and fail-closed production factories.
- Claude's own-test lock, infrastructure-failure classification/retry, drift/probe
  stages, accounts/webhooks, model-problem support and report image metadata.
- Online execution in a fresh sandbox branch with canonical batch predictions
  removed; the verifier validates output count/types/ranges and computes the comparison.
  The adapter now supports multiclass labels, regression values and custom loaders.
- The API-based CLI, updated to the new token/workspace headers. No dotenv helper
  or direct unisolated production-agent invocation is restored.

**BUG-05 remains partial.** Comparing raw online values outside the candidate process
fixes self-authored verdicts; it does not complete a general external sealed-test
protocol. Legacy candidate-imported sealed pytest remains fail-closed in production.
No claim that arbitrary hostile Python cannot deceive every permitted protocol.

## Validation and reproduction

No paid provider inference or real GitHub writes were used. Test bootstrap disables
dotenv loading. `backend/tests/repair_ui_server.py` is a dedicated localhost-only QA
server, never a production route: real FastAPI routes, SQLite, account membership,
intake, durable worker, authored scripted model replies, software and ML oracles.
It seeds fake accounts and writes ephemeral QA credentials outside the repository.
The test uses the explicit unisolated test runner; it does not prove Docker/service
isolation or successful Nemotron reasoning. Scripted runs must never be published
as genuine model runs.

```powershell
python backend/tests/repair_ui_server.py --state-dir <absolute-temporary-QA-directory>
# Build/start frontend with API_INTERNAL_URL=http://127.0.0.1:8112.
$env:E2E_WEB_URL='http://localhost:3112'
$env:E2E_REPAIR_HANDOFF='1'
$env:E2E_REPAIR_IDENTITY='<absolute-temporary-QA-directory>/identity.json'
npx playwright test e2e/repair-handoff.spec.ts --workers=1
```

Use a separate Playwright output directory for concurrent invocations; default
output directories collide. Do not target the QA tests at a production service.
Results are recorded below.

## Still open

- Editable per-task budget controls/admission estimates and continuous source/tool/token
  telemetry need backend API/worker publication beyond this handoff (FE-06/07).
- Genuine multi-fixture agent runs, matched baselines/ablations and an independent real
  repository/user pilot remain evidence work (FE-02/03/13, EV-02/03/04, BUG-09).
- BUG-12 successful account-level provider verification remains unperformed.
- Real GitHub OAuth and App installation/webhook-to-draft delivery were not exercised;
  backend tests use mocked external GitHub. Account authorization and local link APIs
  were exercised against the real backend.
- Several backend criteria are narrower than their original wording: file holdout
  permits rather than Postgres; literal-probe ranking rather than information gain;
  candidate artifact persistence at completion; concurrent spend-reservation assurance.
  The backlog preserves these limitations instead of copying blanket “Done” labels.

Hosting, provider-key setup and demo-video production remain the owner's scope.

### Completed checks so far

- Fast repair suite excluding Docker module: **164 passed, 17 skipped, 22 deselected**.
- Account/webhook/model-problem and bug regression files: **37 passed**, including
  three fresh-adapter custom-loader cases. The updated CLI header assertions also
  passed separately: **2 passed** (overlapping coverage).
- Docker runner module on this host: **28 passed, 1 skipped**.
- Final production build passed, including `/auth/callback`, and TypeScript validation.
- Real full source→task→worker→verification→receipt browser test passed in the first
  integration run. Final repeated browser/slow-suite results follow below.

Earlier browser retries corrected two test-harness problems (Next's route announcer
matching a broad alert locator, and an init script resetting workspace state on every
navigation). Another run failed while writing Playwright trace ZIPs, rather than a
product assertion. Final browser execution disables trace recording; accessibility,
interaction and API assertions remain enabled. Counts overlap and are not summed.

- Full slow repair suite: **22 passed, 210 deselected**, 22m09s. All five added
  fixture proofs were regenerated against the combined verifier and remain green.
- Repeated real-API browser suite: **3 passed, 2 skipped** (the skipped tests belong
  to the separate mocked OAuth lane), 6.9m. Covers member changes/last-owner rule,
  CI link CRUD, workspace creation/switching, viewer write refusal/cross-workspace
  refusal, desktop/mobile accessibility, and a complete scripted repair through
  actual durable execution and final independent receipts.
- The final production build includes a fix for a real expired-session recovery
  race discovered during testing: the sign-in message now survives the identity
  transition and clears on successful sign-in/sign-out.
- Legacy accessibility checks now wait for finite entrance animations to settle
  before measuring contrast, without disabling contrast rules.

The QA worker's scripted repair took several minutes on this host. This is not
production latency or Nebius-model performance evidence.

- Final offline browser suite: **29 passed, 3 skipped**, 1.5m. The three skips are
  the opt-in real-server scenarios, validated separately above. Includes OAuth
  state/headers, persistent expired-session recovery, compatible contract metrics,
  group tables/relative changes, replay boundaries, dark/light/mobile accessibility
  and the legacy UI. Trace capture was off; no accessibility rules were disabled.

The scripted QA worker was stopped after validation. The running local preview
was restored to replay-only mode with no provider credentials.
