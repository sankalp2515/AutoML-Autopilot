# Autopilot input, sharing and monitoring contracts

## Score inputs and supported models

Two CSV windows are required. Reference outcomes have two values; current outcomes may be blank. Choose the positive class explicitly. Optional `score_column` names probabilities of that positive class in both windows. If omitted, a column named `score` is detected automatically. Scores must be finite, within `[0,1]`, and present on every row. Scores and the outcome are never input features.

Reference/current quality, threshold studies and threshold-only corrections use real scores when supplied. Input repairs and retrains create a reconstructed model candidate. The receipt's `prediction_sources` records both sources; it does not claim the user's original model was executed after an input repair. Supported stand-ins: logistic regression, histogram gradient boosting, random forest, and XGBoost/LightGBM when installed. The public runtime image does not require either optional package.

Rows stay in backend files. Nemotron receives findings, column names, team notes and relevant research context. Tavily receives sanitized library queries and listed documentation URLs. Notes and documents can contain sensitive material: remove secrets before submitting them. Automatic free-text secret/PII minimization remains a readiness item.

## Retention and deletion

`AUTOPILOT_UPLOAD_TTL_HOURS` defaults to 24. The worker periodically selects expired uploads and deletes raw CSVs, linked terminal investigations, events and derived artifact workspaces. Active/leased investigations defer deletion. The same cascade is used by the upload history's delete action. Tests cover ownership, raw files and derived-state removal. Inline development mode has no worker cleanup loop; use explicit delete or run the cleanup service method.

Database/filesystem deletion is not a single transaction. Concurrent creation versus deletion needs a tombstone workflow before production-scale claims; see W13 in the readiness backlog. Backups are not automatically purged by this application policy.

## Read-only sharing

An owner can create a share capability for a completed investigation. It expires in 24 hours. The signed payload contains the run ID and expiry; HMAC verification uses a configured `AUTOPILOT_SHARE_SECRET` or a random key persisted as `DATA_DIR/.share-key`. Multiple API replicas need a common signing key or a shared data directory. Losing/rotating the key invalidates existing links.

The public endpoint returns a completed report with server model paths removed and `Cache-Control: no-store`. The share page sets no-index/no-follow and no-referrer metadata. Bearer links do not authorize list, next, cancel, share or review endpoints. Sharing includes findings, team notes and research; it does not expose raw CSV downloads. The UI explains that disclosure before copying the link. Individual token revocation is not yet implemented.

## Monitoring adapter

The hook deliberately references an **existing configured upload**. It does not fetch arbitrary CSV URLs. An Evidently/Arize integration maps its source event into this contract:

```http
POST /api/v1/autopilot/hooks/alert
X-Alert-Token: <configured integration secret>
X-Autopilot-Session: <session that owns the upload>
Content-Type: application/json

{"upload_id":"012345abcdef","source":"evidently","alert_id":"quality-alert-42"}
```

Configure `AUTOPILOT_ALERT_TOKEN` server-side; empty means disabled. Normal authenticated tenant scope is also supported. The server hashes `source:alert_id` into an idempotency key, validates ownership and configuration, and applies ordinary creation limits. Replaying an event returns the same run. Alert source/title text is not interpreted as executable policy.

Poll the investigation endpoint for progress. `GET /api/v1/autopilot/investigations/<id>/summary` returns `text`, Slack-compatible `blocks` using `plain_text`, and the next action. This is a payload for an authorized integration to send; the app itself does not message Slack or any third party.

## Scale and evidence limits

- Creation-rate limits are process-local (`guard._creates`); one API process is the supported enforcement boundary. Browser sessions can be rotated. A multi-replica service needs shared admission counters and independent abuse controls.
- Daily provider usage is persisted, but check-then-start does not atomically reserve in-flight spend.
- Worker claim leases prevent duplicate claims in tested cases; state-write fencing for stale workers remains work.
- A verifier service and read-only artifact mount do not by themselves make final labels inaccessible to the worker or make holdout use durably single-shot.
- Use the separate readiness backlog for these production limits rather than assuming horizontal scalability from the presence of Postgres.

Set `NEXT_PUBLIC_SITE_URL` at frontend build time to the intended origin for social preview URLs. This is a metadata setting, not a hosting action. The application currently falls back to localhost for local builds.

## Reproduce the evidence

```text
python scripts/prepare_adult_incident.py
python scripts/record_product_evidence.py
cd backend
python -m pytest tests/test_autopilot -q
cd ../frontend
npx tsc --noEmit
npm run build
```

The Adult preparation script also accepts `--archive /path/to/adult.zip` for a separately downloaded official archive. The checked-in fixture lets all investigations run offline. The provenance manifest records upstream archive and transformed sample hashes and attribution.

For browser contracts build with `NEXT_PUBLIC_AUTOPILOT_ONLY=true`, `NEXT_PUBLIC_API_URL` set to an empty string, and no Supabase configuration; run `E2E_MOCK_API=1 npx playwright test e2e/autopilot.spec.ts` (PowerShell: set these through `$env:NAME`). This suite uses recorded API fixtures; backend tests exercise real investigation execution. CI runs both SQLite and Postgres backend tests plus the production frontend build and browser suite. A fully composed CI browser workflow remains W23.
