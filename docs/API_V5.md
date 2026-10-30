# v5 repair API — frontend reference

Base path: `/api/v1/v5` (the web app proxies `/api` to the api service). All bodies are JSON
unless marked *multipart*. Unknown fields are rejected (422). Source of truth:
`backend/app/repair/routes.py`.

## 1. Identity headers

| Header | When | Meaning |
|---|---|---|
| `X-Autopilot-Session` | always (anonymous demo) | Random per-browser id (≤64 chars). Without a sign-in token this browser is its own tenant and acts as owner of its own runs. |
| `X-Autopilot-Token` | after GitHub sign-in | Session token from `/auth/github/callback`. Valid `REPAIR_SESSION_HOURS` (default 7 days). Expired or bad → **401**: send the user to sign in again. |
| `X-Workspace` | signed in, optional | Workspace id to act in. Default: the account's personal workspace. Not a member → **404**. |

Roles inside a workspace: `viewer` < `member` < `admin` < `owner`.
- **viewer**: every GET.
- **member**: stage repos, upload packs, start/cancel tasks, patch/PR/publish.
- **admin**: manage members (not owners) and GitHub links.
- **owner**: everything; a workspace always keeps at least one owner.

Missing role → **403** `{"detail": "this needs the member role in the workspace"}`. Hide or
disable controls by `role` from `GET /me` instead of waiting for the 403.

Signed-in users need no invite code for live runs; anonymous users may (see `/status.invite_required`).

## 2. Sign-in and workspaces (BE-23)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/auth/github` | — | `{url, state}`. Redirect the browser to `url`. 503 if sign-in is not configured → hide the button. |
| POST | `/auth/github/callback` | `{code, state}` from the GitHub redirect query | `{token, account, personal_workspace, workspaces}` (401 if expired/forged) |
| GET | `/me` | — | `{account, personal_workspace, workspaces, current_workspace, role}` (401 when anonymous) |
| POST | `/workspaces` | `{name}` | `{id, name, role: "owner", tenant}` |
| GET | `/workspaces/{id}/members` | — | `{members: [{account_id, login, name, avatar_url, role}]}` |
| PUT | `/workspaces/{id}/members/{login}` | `{role: "viewer"\|"member"\|"admin"\|"owner"}` or `{role: null}` to remove | `{login, role}` |

- `account`: `{id, login, name, avatar_url}`; `workspaces[]`: `{id, name, role, tenant}`.
- For member routes, send `X-Workspace` equal to `{id}` (otherwise 404).
- Member errors: 422 `"<login> has not signed in to AutoML Autopilot yet"`, 422 `"a workspace must keep at least one owner"`, 403 for role limits. Show `detail` as is.
- The GitHub redirect URL is `GITHUB_OAUTH_REDIRECT_URL` (a web page, e.g. `/auth/callback`) that POSTs `code` + `state`, stores `token`, then calls `/me`.

## 3. Ops

| GET | Returns |
|---|---|
| `/status` | `{provider, models, configured, live_runs, invite_required, runner, isolated, verifier, tavily, daily_cap_usd, successful_calls_7d, failed_calls_7d, last_model_used, last_successful_call_at, worker_seen_s_ago}` |
| `/ready` | `{database, worker, verifier, sandbox, ready}`; values `ok` / `stale` / `down` / `in-process` |
| `/metrics` | `{window_hours, tasks, queue_depth, oldest_queued_s, finished, decisions: {VERIFIED: n, ...}, run_seconds_p50, run_seconds_p95, model_tokens, cost_usd, known_cost_usd, unknown_cost_runs}` (last 24 h; `?format=prometheus` for text) |

## 4. Repositories and contracts

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/samples` | — | `{samples: [...]}` bundled demo repos |
| POST | `/repos` | `{sample}` or `{url, ref}` | 201, returns a **repo description** (below). URL must be https on GitHub/GitLab/Bitbucket. |
| POST | `/repos/upload` | *multipart* `file` (zip) | 201, repo description |
| GET | `/repos/{repo_id}` | — | repo description |
| PUT | `/repos/{repo_id}/contract` | `{yaml, fail_to_pass?: [node ids]}` | saves `autopilot.yaml`; returns repo description |
| POST | `/repos/{repo_id}/discover` | — | runs the suite once in the sandbox; returns `{outcomes: {node_id: passed\|failed\|error\|skipped}, failing: [node ids]}`. Let the user tick from `failing`. |

Repo description: `{id, source, upstream_sha, sha, files[≤500], file_count, unpinned_requirements, sample, contract_yaml, contract_errors[], contract_source: "repository"|"edited"|"proposed", needs_input?[], tests}`.
Show `contract_errors` and `needs_input` next to the editor; the task can start only when `contract_errors` is empty.

### New contract fields (BE-25) the editor should know

```yaml
data:
  problem: binary            # binary | multiclass | regression (default binary)
  positive_label: 1          # binary only
task:
  metric: roc_auc            # binary: roc_auc f1 recall precision balanced_accuracy accuracy
                             # multiclass: accuracy macro_f1 balanced_accuracy
                             # regression: rmse mae r2
entrypoints:
  serve: pkg.serve:score     # fn(payload: dict, model)
  load_model: pkg.io:load    # optional fn(path) -> model, for non-sklearn models
```
- Predictions file: binary `id,score[,decision]`; multiclass and regression `id,prediction`.
- A metric that doesn't fit the problem is reported in `contract_errors`.
- `task.fail_to_pass` may be empty when `ml_invariants` is set (a "metric dropped" task). The agent then profiles data drift first.

## 5. Evaluation packs (holdout labels)

| Method | Path | Body |
|---|---|---|
| POST | `/packs` | *multipart* `file` (CSV), `id_column`, `target`, `positive_label` (default "1"), `segment_column?` → 201 `{id, rows}` |
| DELETE | `/packs/{pack_id}` | 204 |

Labels never come back from the API. Without a pack the verdict can be `VERIFIED` with reason
`LIMITED_NO_LABELS`: say "quality on unseen data was not measured".

## 6. Tasks

`POST /tasks`:
```json
{"repo_id": "abc123def456", "target": "Online scoring raises after the refactor",
 "fail_to_pass": ["tests/test_serve.py::test_x"], "pack_id": null, "mode": "full", "invite_code": null}
```
- `mode` is `full` (our agent) or `generic` (comparison arm).
- 403 with a readable `detail` when live runs are off, the invite code is missing, the daily task limit is reached or the budget is used up. Offer the replays in that case.

**Task object** (create, `GET /tasks`, `GET /tasks/{id}`): `{id, status, version, contract_digest, target, event_seq, cancel_requested, created_at, engine: "v5", mode, sample, source, repo_name, fail_to_pass, ml_invariants, has_labels, decision, reasons, finalist, cost_usd, seconds, error}`.

Status:
- Live: `CREATED SNAPSHOTTING MAPPING INVESTIGATING DIAGNOSING PATCHING CODE_VERIFYING ML_VERIFYING SELECTING`.
- Final: `REVIEW_READY` (verified), `PR_DRAFTED`, `REJECTED`, `INCONCLUSIVE`, `BLOCKED`, `FAILED`, `CANCELLED`.

| Method | Path | Notes |
|---|---|---|
| GET | `/tasks?limit=50&offset=0` | list for the current tenant/workspace |
| GET | `/tasks/{id}/events?after=<seq>&limit=200` | poll every 1–2 s with the last `seq` you saw; append |
| GET | `/tasks/{id}/candidates` | candidates with steps, stop_reason, eligible, tokens |
| GET | `/tasks/{id}/candidates/{cid}/diff` | text diff |
| GET | `/tasks/{id}/candidates/{cid}/receipt` | code / ml_screen / ml / combined receipts |
| GET | `/tasks/{id}/report` | full report; **409** until the run finishes |
| POST | `/tasks/{id}/cancel` | `{version?}`; 409 on a stale version |

**Event types** (`type` field): `task.created repo.snapshotted repo.mapped baseline.reproduced hypothesis.proposed hypothesis.revised tavily.source.retrieved diagnostic.executed candidate.created patch.applied tool.denied tests.failed tests.passed candidate.frozen code_oracle.passed code_oracle.rejected ml_oracle.passed ml_oracle.rejected candidate.verified candidate.rejected budget.exhausted pr.drafted task.cancelled task.failed`.

New in this release:
- `diagnostic.executed` with `kind: "probe"`: `{hypothesis_id, path, pattern, expect, outcome: confirmed|refuted|unrunnable}`. Show it as a check-mark or cross on the hypothesis card.
- `diagnostic.executed` with `kind: "data_drift"`: `{columns, flagged: [col]}`.
- `hypothesis.revised`: `{order: ["H3","H1"], statuses}`. Re-order the hypothesis cards.

**Report additions:**
- `hypotheses[].status` and `hypotheses[].probe`.
- `data_profile: {columns, flagged: [{column, psi, missing_shift}]}`.
- `image` (candidate image digest).
- `pull_request: {url, number, branch} | {error}` for GitHub-triggered tasks.
- `maintainability_notes: [string]`: static reviewer prompts, not a verification gate or certification. Added for BUG-14.

ML receipt metrics:
- `metrics.holdout: {metric, candidate, baseline, rows}`.
- `metrics.delta: {delta, ci_low, ci_high, relative}`. `delta` is always "improvement" (positive = better). When `relative` is true (rmse/mae), show it as a percentage.
- `metrics.segments: {column, metric, by_group: {g: {candidate, baseline, delta, rows}}, too_small}`.

## 7. Delivery and replays

| Method | Path | Notes |
|---|---|---|
| GET | `/tasks/{id}/patch` | `.patch` download; 409 unless VERIFIED |
| POST | `/tasks/{id}/pr` | `{token, repo: "owner/name", base_branch?, title?}` → `{url, number, branch, commit}`; token used once, never stored; only for repos added by URL |
| POST | `/tasks/{id}/publish` | `{replay_id}` |
| GET | `/replays` | `{replays: [...]}` (no auth; zero cost) |
| GET | `/replays/{id}` | the recorded report and events; play them with the same UI as a live task |

## 8. GitHub App (BE-24)

| Method | Path | Role | Notes |
|---|---|---|---|
| GET | `/github/links` | signed in | `{links: [{repo, workspace_id, installed, auto_pr, created_at, recent: [{event, outcome, task_id, at}]}]}` |
| POST | `/github/links` | admin | `{repo: "owner/name", auto_pr: true}` → 201; 409 if another workspace owns it |
| DELETE | `/github/links/{owner}/{name}` | admin | 204 |
| POST | `/github/webhook` | GitHub only | not for the frontend |

UI hints:
- `installed: false` means no webhook has arrived from the App installation yet. Show "Install the GitHub App on this repository".
- `recent[].outcome` is one of: `queued`, `task created` (link `task_id`), `skipped: the repository has no valid autopilot.yaml`, `not admitted: …`, `failed: …`.
- GitHub-triggered tasks have the target `CI failed: <workflow> on <branch>`.

## Budget and live progress additions

`POST /budget/estimate` accepts `{max_candidates:1..2,coder_tokens:1024..160000}`
(member role). Returns admitted limits, an estimated nominal token-ceiling inference
cost, pricing provenance and limitations. These are reductions from server policy;
they cannot increase global limits. Optional `budget` on `POST /tasks` has the same
shape and is sealed into the ECC. Other tier and runtime limits stay server policy.

Agent reports use `candidate.progress` carrying `{candidate_id,step,diff,diff_truncated,skipped_files,tokens}`.
The diff preview is capped at 64 KB and is not a frozen final candidate. `usage.updated`
contains the measured `usage` ledger and `tool_calls`. Poll/resume these events by seq
like existing events. The persisted API keeps the frozen Event enum: both messages
use `type: diagnostic.executed` with `data.kind: telemetry`, `data.telemetry_version: 1`
and `data.telemetry_type: candidate.progress | usage.updated`. The other payload fields
are in `data`. The frontend normalizes that envelope. Direct event types in recorded
agent reports remain supported. Final candidates/receipts stay immutable and separate.

## October 30 admission and operations additions

- `POST /tasks` accepts an optional `Idempotency-Key` header (1–128 characters). Keep it across transport/admission retries of the same semantic body. Same tenant/key/body returns the existing task; a different body returns 409. Change the key when the user's intended task changes. The frontend implements this automatically. Cold dependency preparation can take minutes; a timeout is not evidence the task was never created.
- Task admission binds the prepared dependency image in ECC before verifier registration. A task awaiting registration is not claimable by workers; retry the same key if registration is unavailable. Queue/admission waits expire with `E_QUEUE_TIMEOUT`.
- Anonymous custom-repository live tasks require configured invite access or sign-in; anonymous sample access remains subject to the global live switch and caps.
- Global daily estimated dollar/token admission uses atomic reservations. Missing provider usage keeps its reservation and closes the affected tier to more calls. `cost_usd` is null when a finished report lacks complete cost accounting; `known_cost_usd` is the known subtotal, and `unknown_cost_runs` its missing-accounting denominator. Never present null as zero/free.
- `ml_screen` and final `ml` are distinct persisted receipt entries. Final holdout permits and verifier admissions never expire. A worker token cannot register admissions or call the trusted evaluator.
- Dependency preparation now requires exact top-level pins and an operator-approved HTTPS package index. URLs, version ranges, nested requirement includes, local/source installations and arbitrary pip flags are refused. Readable infrastructure errors must not be presented as a code rejection.
- `DELETE /repos/{repo_id}` and `DELETE /packs/{pack_id}` require the owning tenant and refuse active task references. TTL and hard-disk-quota limitations are described in [V5_OPERATIONS.md](V5_OPERATIONS.md).
