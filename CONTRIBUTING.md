# Contributing

Start with [README](README.md), [as-built architecture](docs/V5_ARCHITECTURE.md), [API](docs/API_V5.md) and [backlog](docs/V5_BACKLOG_FINAL.md). v5 repository repair is the primary product; earlier modes are documented in [legacy scope](docs/LEGACY_SCOPE.md).

## Work safely

Create a focused branch or worktree. Do not commit `.env`, credentials, uploaded repositories, holdout labels or private test data. Use synthetic/public fixtures. Never run schema-recreating tests against an application database. Preserve upstream licenses and evidence hashes. Generated code must be reviewed like any other change.

`backend/app/repair/contracts.py` and `sandbox/base.py` are frozen for the current integration. Propose protocol changes in the PR description; document required adapter capabilities rather than silently breaking callers. Keep agent/model authority separate from trusted grading. A missing check is not a passing check.

## Checks by change

From `backend/`, install `requirements-runtime.txt`, pytest and pytest-asyncio. Run the fast suite with `python -m pytest tests/test_autopilot tests/test_repair -q -m "not slow and not docker"`. For transactional changes, run with both disposable `REPAIR_TEST_POSTGRES_URL` and `REPAIR_TEST_LEDGER_URL`. Confirm expected skips explicitly. For sandbox changes, run `python -m pytest tests/test_repair/test_sandbox_local.py -q` against Linux Docker. For agent/checker changes, run `python -m pytest tests/test_repair -q -m slow`.

From `frontend/`, use the lockfile (`npm ci --legacy-peer-deps`), `npx tsc --noEmit`, and `npm run build`. Set `E2E_MOCK_API=1` and `E2E_WEB_URL=http://localhost:3140`, then run `npx playwright test e2e/repair.spec.ts e2e/landing.spec.ts --workers=1`. The mock-mode config starts the built app; install Chromium with `npx playwright install chromium` if needed. For a real keyless stack, set `E2E_REPAIR_LIVE=1`, `E2E_WEB_URL` to its URL and run `e2e/repair-live.spec.ts`; screenshot capture is optional. Review desktop/mobile, both themes, keyboard focus and axe results.

These deterministic checks make no model calls. Genuine evaluation is an explicit separate operator action: use a fresh study directory, frozen prompt/model/limits, scoped credentials and a disclosed spending allowance. Never mix engines into an existing study or relabel NVIDIA results as Nebius.

## Pull requests

Explain the concrete problem, resulting behavior, relevant checks/results and material limits. Link backlog IDs. Include screenshots for visual changes. Describe receipt/admission/tenant compatibility and migration implications where relevant. Do not upload secrets or private payloads in logs. Check the license and attribution of every new dependency or dataset. Avoid unrelated changes; update affected docs and backlog statuses with scoped evidence.

No PR should claim a cloud deployment, model benefit, accessibility study or benchmark it did not actually verify. Use [support](SUPPORT.md) for reproducible bugs and [security reporting](SECURITY.md) for sensitive issues.
