# AutoML Autopilot

**When an ML system fails, Autopilot runs the experiment that teaches you the most, then verifies a
fix before anyone ships it.**

It takes an incident (a degraded model, a failed retrain, or a teammate's challenger), measures it,
lists competing causes, and runs the cheapest approved test that tells them apart. When one cause
is supported, it builds a bounded correction and hands it to an independent verifier on a frozen,
single-use holdout. The verdict is one of CORRECTION_VERIFIED, REJECT, INCONCLUSIVE, BLOCKED or
DIAGNOSIS_SUPPORTED. Rejecting a bad fix, or saying there isn't enough evidence, counts as a
correct answer.

> Status (2026-10-28): the core loop, API, UI, tests and benchmark all work, and both external
> services run live. **Tavily** Search + Extract and the **Nemotron** planner (Nemotron 3 Ultra via
> the NVIDIA API catalog) were exercised end to end on S02–S12 and T01. **Nebius Token Factory**
> is still on hold (API key issue); the adapter is the same code and takes over as soon as
> `NEBIUS_API_KEY` is set. Without keys the app runs and **says so on screen**. It never fakes a call.

## The problem

When a production model degrades or a retrain breaks, the usual response is to retrain and hope.
That is expensive, it often fixes nothing (a unit bug survives a retrain), and a bigger number on
an unchecked split can ship a model that is worse for some customers. AutoML tools optimise a
model; nothing helps you find *why* it broke and prove a fix before shipping it.

Autopilot treats the incident as a diagnosis problem: list the causes that could explain the
evidence, run the cheapest approved test that tells them apart, fix only the supported cause, and
let an independent verifier decide on data the fix never saw.

## User flow

1. **Pick an incident** at `/autopilot` (a degraded model, a failed retrain or a teammate's
   challenger). Choose auto or step mode, the planner (Nemotron or rules-only ablation) and
   whether Tavily research may be used.
2. **Measure.** Splits are frozen and hashed, the baseline is rebuilt and findings are computed
   (metric drop, drift, missingness, pipeline errors, segment spread). Each finding shows its method
   and sample count.
3. **Research, only if needed.** A library error with no local answer triggers a sanitised Tavily
   search: official scikit-learn domains first, the open web only if that finds nothing. Forums are
   shown for context but never used for a fix.
4. **Hypothesise.** Nemotron proposes 2–4 competing causes citing real finding IDs. If it omits a
   cause the rules score highest, that cause is added and labelled *added by rule*.
5. **Test.** Each candidate test is scored by expected information gain ÷ cost; the table shows why
   the chosen test beats a retrain. The test runs in a killable child process and beliefs update.
6. **Fix.** Once one cause is supported, the smallest matching fix is built (input repair, new
   threshold, sourced parameter rename, …). Retrains only happen if the cause calls for one.
7. **Verify.** The verifier checks artifact, protocol parity, leakage, sample size and metric
   provenance, opens the final test once, and requires a paired-bootstrap CI and a critical-segment
   guardrail to pass.
8. **Decide.** The verdict card summarises root cause, fix, held-out result and spend. A reviewer
   can approve or reject (recorded only; no live model changes) and export the JSON receipt.

## Quick start

```bash
cp .env.example .env          # set POSTGRES_PASSWORD, AUTOPILOT_VERIFIER_TOKEN and a Nemotron key
docker compose up -d --build  # five services; see DEPLOYMENT.md
open http://localhost:3000
```

For development without Docker (runs investigations inside the API process, SQLite):

```bash
cd backend && pip install -r requirements-runtime.txt
python ../scripts/dev_autopilot_backend.py          # API on http://localhost:8000/docs
cd ../frontend && npm install && npm run dev          # UI on http://localhost:3000/autopilot
```

### Environment variables (all optional)

| Variable | Default | Effect |
|---|---|---|
| `TAVILY_API_KEY` | empty | Turns on runtime Tavily Search + Extract. Empty → research shows `UNAVAILABLE`. |
| `NEBIUS_API_KEY` | empty | Nemotron planner via Nebius Token Factory. Takes priority when set. |
| `NEBIUS_PLANNER_MODEL` | `nvidia/Nemotron-3-Ultra-550b-a55b` | Reasoning model for hypotheses (Token Factory catalog ID). |
| `NEBIUS_FAST_MODEL` | `nvidia/Nemotron-3_5-Lightning` | Small model for the stakeholder summary. |
| `NVIDIA_API_KEY` | empty | Nemotron planner via the NVIDIA API catalog, used when no Nebius key is set. No key at all → deterministic planner, labelled. |
| `NVIDIA_PLANNER_MODEL` | `nvidia/nemotron-3-ultra-550b-a55b` | Verified callable on 2026-10-28 (3 Super is listed but returned 404 for this account). |
| `NVIDIA_FAST_MODEL` | `nvidia/nemotron-3.5-lightning-30b-a3b` | Small model on the NVIDIA API path. |
| `AUTOPILOT_CREATES_PER_10MIN` / `AUTOPILOT_DAILY_LLM_CALLS` / `AUTOPILOT_DAILY_TAVILY_CALLS` / `AUTOPILOT_IDLE_EXPIRY_MIN` | 6 / 400 / 200 / 15 | Public-demo guards: create rate per visitor, daily provider budget, idle step-mode expiry. |
| `NVIDIA_ENABLE_THINKING` | `false` | Nemotron 3 reasoning trace. Off: 10–30 s per plan instead of 40–80 s; the reply is schema-checked either way. |
| `NEBIUS_BASE_URL` | `https://api.tokenfactory.nebius.com/v1/` | OpenAI-compatible endpoint. |
| `AUTOPILOT_RUNNER` | `process` | `process` = killable child process per experiment; `inline` for tests and benchmarks. |
| `AUTOPILOT_EXPERIMENT_TIMEOUT_S` | `120` | Wall-clock limit per experiment. The child process is killed when it expires. |
| `AUTOPILOT_MAX_EXTRACTS_PER_RUN` | `3` | Upper bound on Tavily Extract URLs per investigation. |

## How one investigation works

```
intake → frozen protocol → baseline + findings → (research if an evidence gap) → hypotheses
      → rank tests by information gain ÷ cost → policy gate → bounded run → belief update ─┐
      ↑────────────────────────── until one cause is supported ─────────────────────────┘
      → bounded correction → independent verifier → decision receipt → (human review: record only)
```

| Stage | Module | What makes it trustworthy |
|---|---|---|
| Protocol | `mlcore.make_protocol` | Splits are frozen before any fitting, assigned by a keyed hash and recorded as `protocol_hash`. |
| Findings | `profiler.py` | Machine-computed aggregates only, each with a method and a sample count. No raw rows leave. |
| Hypotheses | `planner.py` | Picked from a closed list of causes. Nemotron output is schema-checked, and every finding or source ref it cites must exist. The two causes the rules score highest are always included (`rule_anchor`). |
| Selection | `beliefs.py` | `IG(E) = H(b) − Σ_o P(o\|E)·H(b\|o)` from a typed prediction matrix. Belief weights are labelled *uncalibrated*. |
| Admission | `policy.py` | Only allow-listed templates and bounded parameters. Checks hypothesis refs, accepted sources, budget and duplicate signatures. |
| Execution | `runner.py`, `templates.py` | Vetted template code only. Runs in a child process that is killed on timeout. |
| Verification | `verifier.py` | Recomputes metrics from the artifact. Checks protocol parity, leakage, sample size, the final test (opened once), paired bootstrap CI and the critical-segment guardrail. |
| Research | `research.py` | Gap detector, then a query built from library identifiers only (fails closed). Official domains are searched first. Source gate checks publisher, version and injection; third-party pages are `CONTEXT_ONLY`. A cited source can only supply a parameter name that the installed library confirms. |
| Decision | `engine.py` | Explicit state machine: an illegal transition raises. Every failure path ends FAILED or INCONCLUSIVE, never "success". |

## How Nemotron is used (and checked)

| Role | Model | What it does | How it is checked |
|---|---|---|---|
| Plan hypotheses | Nemotron 3 Ultra | Reads measured findings **with their values**, the incident report and the team's notes (free text rules cannot use), and proposes 2–4 competing causes | Schema check; every finding or source it cites must exist; one corrective retry with the exact error, then an honest fallback to rules |
| Stakeholder summary | Nemotron 3.5 Lightning | Turns the verified decision into 2–3 sentences for a manager | Every number in the text must appear in the verified facts, otherwise the summary is discarded |

Each call is shown in the UI's *Model calls & cost* panel with model, provider, latency, tokens and an
estimated cost at Nebius Token Factory list prices (`autopilot/pricing.py`). A typical run costs about
$0.003. The models never run code, choose tests, or report metrics: the selector, policy gate, runner and
verifier are deterministic.

## Trust records (v4.1)

- **Provenance on every item**: findings are `observed` (measured by code), Tavily pages are `retrieved`,
  hypotheses are `inferred`.
- **Experiment envelope**: at admission each experiment is written as an envelope (dataset digest, split
  and evaluation protocol digests, template@version, parameters, expected outcomes, cited sources, resource
  ceiling, authorising policy) and digested over every field except the digest. The verifier refuses to run
  if the envelope no longer matches (`E_ENVELOPE_MISMATCH`). This is an integrity check, not a signature.
- **Verifier process**: experiment processes cannot load final-test rows (`PermissionError`); only the
  verifier, in its own process, can, and the engine records the single open. Same-host process isolation,
  not separate machines.
- **Fail-safe restart**: a server restart mid-run ends the run `FAILED / E_INTERRUPTED` with the incumbent
  unchanged; it is never reported as a success. Step-mode runs survive for the user.

## Public demo safety

Each browser gets its own anonymous session, so one visitor's open runs never block another's. Step-mode
runs left idle for 15 minutes close themselves. New investigations are rate-limited per visitor, Nemotron
and Tavily calls have a daily budget (after which runs use the rules planner and say so), and a queued run
shows its position instead of an error.

## Incident catalog

The data is seeded synthetic loan-default data. The fault oracle lives in `scenarios.py` and only
the benchmark and tests read it.

| ID | Incident | Expected verdict |
|---|---|---|
| S01 | Healthy model | INCONCLUSIVE: no action warranted |
| S02 | Upstream sends `income` in thousands | Parity test → unit fix → CORRECTION_VERIFIED |
| S03 | Serving threshold changed | Threshold candidate → CORRECTION_VERIFIED |
| S04 | Null surge and unseen category | BLOCKED: do not retrain on invalid input |
| S05 | Label prior shift | Prior check → threshold → verified |
| S07 | Unlabelled drift | INCONCLUSIVE: drift is real, quality unknown |
| S08 | Challenger uses a post-outcome feature | REJECT (leakage, rejected before the holdout is spent) |
| S09 | Challenger scored on its own split | REJECT (protocol mismatch) |
| S10 | Challenger: headline F1 up, rural F1 0.63 → 0.37 | REJECT (segment guardrail) |
| S12 | 80 labelled rows | INCONCLUSIVE: no test can discriminate |
| T01 | `OneHotEncoder(sparse=…)` fails after a scikit-learn upgrade | API probe → Tavily release notes → sourced rename → verified (non-inferiority). Without Tavily: DIAGNOSIS_SUPPORTED |

## Benchmark (equal budget, held-out seeds 100–104)

`python -m app.autopilot.benchmark --seeds 5` writes `docs/benchmarks/autopilot_benchmark.json`.
The same results appear in the UI at `/autopilot/benchmark`.

| Arm | Correct decisions | False promotions | Mean tests | Total cost units | Retrains |
|---|---|---|---|---|---|
| Retrain first (classic AutoML response) | 22/40 | 0 | 0.75 | 300 | 30 |
| Fixed-order checklist | 35/40 | 0 | 2.23 | 143 | 0 |
| Random eligible test | 36/40 | 0 | 2.38 | 171.5 | 0 |
| **Autopilot (IG ÷ cost)** | **40/40** | **0** | **1.25** | **92.5** | **0** |

The verifier must-fail controls (S08, S09, S10): 15/15 rejected, with 0 false promotions.

**Read these numbers honestly.** I designed the scenarios, the prediction matrix and the detection
thresholds, and tuned them on seed 7. The held-out part covers seeds only, not unseen fault types.
The benchmark shows that the selection policy beats simple baselines on these fault classes under
an equal budget. It does not show performance on real production incidents. The benchmark uses the
deterministic planner so it is reproducible offline; a live Nemotron arm and a live Tavily on/off
comparison were **not run** as benchmarks, and the report says why. Live runs are recorded below.

## Live ablation (2026-10-28, held-out seeds 100–104)

`python scripts/run_live_ablation.py --seeds 5` writes `docs/benchmarks/autopilot_ablation_live.json`.
Rules planner 40/40; Nemotron 3 Ultra (forced, no fallback) 37/40, of which 1 was a provider
outage; 0 false promotions in both. On T01, Tavily on gives 5/5 verified sourced fixes and
Tavily off gives 5/5 diagnosis-only. The first run of this ablation found two bugs, both
fixed and covered by tests: a model asked about a healthy model invents causes (now a
deterministic symptom gate runs first), and the compatibility fix re-tuned the threshold
(it now changes only the renamed parameter).

## Live runs (2026-10-28, seed 7, Nemotron 3 Ultra via NVIDIA API, Tavily live)

| Incident | Verdict | Planner call | Notes |
|---|---|---|---|
| T01 | CORRECTION_VERIFIED (non-inferior) | ~10–28 s | Tavily: official scikit-learn page accepted, `sparse → sparse_output` confirmed by the installed signature; forum results shown as context only |
| S02 | CORRECTION_VERIFIED, F1 0.483 → 0.570 | ~20 s | Nemotron omitted the units cause; the rule anchor added it and the parity test supported it |
| S03 | CORRECTION_VERIFIED | ~24 s | |
| S04 | BLOCKED | ~11 s | Invalid input; no training attempted |
| S05 | CORRECTION_VERIFIED | ~18 s | |
| S07, S12 | INCONCLUSIVE | 17–37 s | Unlabelled drift / 80 rows: correctly declined to act |
| S10 | REJECT | — | Challenger review needs no planner; segment guardrail failed |

Every call has a real provider request ID in the receipt, and no key appears in state or receipts.

### Fixes found by running live
- The source gate accepted forums and blogs as evidence. Third-party pages are now `CONTEXT_ONLY`
  and the search tries official domains first.
- The excerpt window missed the rename note on long docs pages; it now centres on a
  "parameter … renamed/deprecated/removed" sentence and strips markdown links.
- Information gain treated "this cause makes no prediction" as an outcome of its own, overstating
  some tests. It now spreads such causes over every outcome, as the belief update does.
- A cause whose rivals were all ruled out by a test it made no prediction for was never marked
  SUPPORTED. Support now counts any earlier consistent observation.
- The threshold study no longer claims to predict the outcome for a unit mismatch (it can go either way).

## API (`/api/v1/autopilot`)

| Method | Route | Notes |
|---|---|---|
| GET | `/scenarios`, `/providers`, `/benchmark` | Provider status reports booleans only, never keys. |
| POST | `/investigations` | Body `{scenario_id, seed?, planner?, research?, max_experiments?, auto_run?}`. Accepts an `Idempotency-Key` header. Returns 429 above 5 unfinished investigations per tenant. |
| GET | `/investigations`, `/investigations/{id}` | Tenant-scoped. Server file paths are stripped. |
| GET | `/investigations/{id}/events?after=` | Append-only timeline. |
| GET | `/investigations/{id}/evidence`, `/experiments` | Findings, hypotheses, belief history, research, rankings, denials. |
| POST | `/investigations/{id}/next` | Step mode: advances exactly one stage. |
| POST | `/investigations/{id}/cancel` | No further experiments; the incumbent is untouched. |
| GET | `/investigations/{id}/receipt`, `/export` | Decision receipt as JSON (download). |
| POST | `/investigations/{id}/review` | Only from REVIEW_READY with a verified receipt. **Records** the decision and changes no model. |

## Tests

```bash
cd backend && python -m pytest tests/test_autopilot -q      # 96 tests, about 90 s
```

The tests cover every scenario against the oracle, IG maths, the policy gate (unknown template,
extra fields, bounds, budget, duplicates, unsourced renames), the runner timeout kill, worker
failure (no false success), cancel, the illegal-transition guard, and Tavily contracts T01–T10
using recorded HTTP fakes: injection, outdated and irrelevant sources, 401/429/500/timeout, no
results, privacy gate, no invented request ids, and no keys in state. They also cover the Nemotron
contract (valid reply, hallucinated finding id, provider failure, unconfigured) and the API
(idempotency, step mode, cancel, review rules, tenant isolation, per-tenant cap).

## Known limitations

- Belief weights are a heuristic. Information gain is exact only given those weights and the
  typed predictions.
- Incidents are synthetic and seeded. Uploading your own incident pack is not built yet.
- The runner is a killable child process running vetted templates. On a laptop it has no network
  sandbox. Docker deployments can host it in the existing no-network sandbox container.
- Review approval is a record only. Autopilot has no path that changes a live model.
- Nebius Token Factory is on hold; the planner runs on the NVIDIA API catalog with the same adapter.
- A Nemotron plan takes 10–30 s; the UI says so while it waits.

## What is new versus the pre-existing AutoML Orchestrator

The whole `backend/app/autopilot/` package, `backend/tests/test_autopilot/`, `frontend/app/autopilot/`,
`frontend/components/autopilot/`, `frontend/lib/autopilot.ts` and `scripts/dev_autopilot_backend.py`
were written on 2026-10-28 for this hackathon. Autopilot reuses the existing FastAPI app, auth and
tenancy, the SQLAlchemy setup, logging and the frontend design system. It does not use the
orchestrator's 10-agent training pipeline.
