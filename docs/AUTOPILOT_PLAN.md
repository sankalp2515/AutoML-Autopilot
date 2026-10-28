# AutoML Autopilot v3.1 — implementation plan and repository audit

Status: working plan, 2026-10-28. Spec: PRD v3.1, System Design v3.1, START_HERE v3.1.
Nebius Token Factory is **on hold** (API key issue). The adapter is built and off by default;
every other part ships as launch-quality code.

## 1. Repository audit (Phase 0 exit gate)

| Spec component | Found in repo | Decision |
|---|---|---|
| FastAPI control plane, auth, tenancy | `app/main.py`, `app/core/auth.py` (`current_tenant`) | **Reuse.** New router mounted under `/api/v1/autopilot`, same auth dependency. |
| Persistence | SQLAlchemy async + `init_db()` create_all, SQLite in tests, Postgres in Docker | **Reuse.** Two new tables: investigations (snapshot) + append-only events. |
| Hypothesis loop | `app/core/investigation.py` (+ primitives) — post-training model audit, LLM picks primitive, severity ranking | **Not reused as-is.** Different job (audits a fresh model, no competing-cause discrimination, no budget/IG). Its pattern (typed catalog + validator + critic) is kept. |
| LLM client | `app/core/llm.py` multi-provider chain | **Not used for Nemotron.** Spec requires exact provider/model attribution; a dedicated Token Factory adapter avoids silent fallback to another vendor. |
| Sandbox | `sandbox/` service runs LLM-written code over HTTP | **Not needed.** Autopilot never executes generated code; templates are fixed Python run in a killable child process with a timeout. |
| Model training/eval | agents + `metric_registry` | Templates use sklearn directly (small, deterministic, testable). |
| Frontend | Next.js 14, `components/ui` kit, `ds-*` tokens, AppShell | **Reuse.** New `/autopilot` routes and components. |
| Tests | pytest + asyncio, SQLite conftest | **Reuse.** New `tests/test_autopilot/`. |

## 2. Product slice (MVP = P0)

One journey: pick or upload an incident → baseline + frozen protocol → findings → competing
hypotheses → information-gain experiment selection → bounded execution → belief update
(loop) → correction candidate → independent verifier → decision receipt → export.

Incidents ship as a seeded catalog so judges reproduce runs without private files. The fault
oracle lives in `autopilot/scenarios.py` and is only read by the benchmark harness.

| ID | Incident | Expected terminal decision |
|---|---|---|
| S01 / B01 | Clean window | No degradation → INCONCLUSIVE ("no action warranted") |
| S02 / B02 | Upstream sends `income` in thousands | Parity diagnostic → rescale correction → CORRECTION_VERIFIED |
| S03 / B03 | Serving threshold changed | Threshold study → validation-chosen threshold → CORRECTION_VERIFIED |
| S04 / B04 | Null surge + unseen category | Contract diagnostic → BLOCKED, no training on invalid input |
| S05 / B05 | Label prior shift | Prior check → threshold/class-weight candidate → verified or reject |
| S07 / B07 | Unlabeled covariate shift | Drift reported, quality unknown → INCONCLUSIVE |
| S08 / B08 | Teammate challenger uses a post-outcome feature | Leakage gate → REJECT |
| S09 / B09 | Challenger scored on a different split | Protocol gate → REJECT |
| S10 / B10 | Challenger improves aggregate, hurts critical segment | Guardrail → REJECT |
| S12 / B12 | 80 labeled rows | INCONCLUSIVE (insufficient evidence) |
| T01 | `OneHotEncoder(sparse=False)` fails after scikit-learn upgrade | API probe + Tavily release notes → sourced compat fix → verified |

B11 (duplicate trial), B13 (malformed proposal), B14 (provider/worker failure), B15 (budget /
cancel) and T02–T10 are covered by unit tests.

## 3. Architecture

```
Next.js /autopilot ──poll──► FastAPI /api/v1/autopilot ──► Engine (state machine, asyncio task)
                                                           │
   ┌──────────────┬──────────────┬─────────────┬──────────┴───┬──────────────┬─────────────┐
   Profiler      Planner        Selector      Policy gate     Runner         Verifier
   (findings)    deterministic  IG / cost     allow-list,     child process  recompute from
                 | Nemotron     + memory      budget, refs    timeout, kill  saved preds,
                 (on hold)      penalty                                      gates → receipt
   Research: gap detector → sanitizer → Tavily Search/Extract → source gate → evidence records
   Store: investigations (snapshot) + events (append-only) + artifacts (sha256) on DATA_DIR
```

Trust rules enforced in code: planner output is schema-validated and every finding/source ref
must exist; external text never reaches the policy gate; only the verifier writes metric
results; final test is a single-access vault; no path mutates an active model (MVP has no
promotion write at all — approval records a reviewer decision only).

### Selection policy
Each hypothesis predicts an observation bucket per template (typed matrix). With belief weights
`b`, `P(o|E) = Σ b_i [pred_i(E)=o]` and `IG(E) = H(b) − Σ_o P(o|E) H(b|o)`. Score =
`IG / (cost_norm + ε) − duplicate_penalty`. Beliefs are labelled **uncalibrated heuristic**.
Update after an observation: consistent hypotheses keep weight, inconsistent ones ×0.05,
renormalise; a hypothesis is SUPPORTED when it is the only consistent one with weight ≥ 0.8.

### Verifier gates (deterministic, no LLM)
protocol hash parity · metric recomputation from saved predictions · leakage (declared feature
contract + single-feature power) · minimum sample / positives · critical-segment guardrail ·
paired bootstrap CI on the final test (lower bound > 0 to pass, else INCONCLUSIVE).

## 4. API (`/api/v1/autopilot`)
`GET scenarios` · `POST investigations` (Idempotency-Key) · `GET investigations` ·
`GET investigations/{id}` · `GET …/evidence` · `GET …/experiments` · `POST …/next` ·
`POST …/cancel` · `GET …/events` · `GET …/receipt` · `GET …/export` · `POST …/review` ·
`GET providers` · `GET benchmark`. Research is triggered by the engine's gap detector rather than
by a separate endpoint, so a client cannot force gratuitous searches (T10). The benchmark runs from
the CLI (`python -m app.autopilot.benchmark`) because it is CPU-heavy.

## 5. Delivery order
1. Core library (schemas, scenarios, profiler, templates, planner, selector, policy, runner,
   verifier) + unit tests.  2. Research layer + tests.  3. Engine + store + routes + API tests.
4. Benchmark harness + report.  5. Frontend.  6. Docs, README, journal, security review.

## 6. Open items needing the owner
- Tavily: live since 2026-10-28.
- Nemotron: live via the NVIDIA API catalog (Nemotron 3 Ultra). Nebius Token Factory still on hold;
  setting `NEBIUS_API_KEY` switches the same adapter over.
- Public repo, hosted tester URL and the < 3 min video are owner actions.
