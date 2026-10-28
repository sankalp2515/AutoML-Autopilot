<div align="center">

# AutoML Autopilot

**When a production model breaks, Autopilot runs the experiment that teaches you the most,
then has an independent verifier prove the fix before anyone ships it.**

NVIDIA Nemotron on Nebius Token Factory · Tavily · FastAPI · Next.js · Postgres · Docker

![License](https://img.shields.io/badge/license-GPL--3.0-blue)
![Tests](https://img.shields.io/badge/autopilot%20tests-96%20passing-brightgreen)
![Services](https://img.shields.io/badge/docker-5%20services-2496ED)

</div>

---

## The problem

Models degrade in production: one study found temporal degradation in 91% of the
model–dataset pairs it tested ([Vela et al., *Scientific Reports* 2022](https://doi.org/10.1038/s41598-022-15245-z)).
When an alert fires, the usual response is to **retrain and hope**. That is expensive.
It often fixes nothing, because a units bug in an upstream feed survives a retrain. And
a bigger number on an unchecked split can ship a model that is worse for some customers.

"AI SRE" agents (Datadog Bits, Resolve AI, Traversal) now investigate *infrastructure*
incidents by forming hypotheses and testing them against logs and traces. Model-quality
incidents need something those tools don't do: **run model experiments, and prove a
model fix is better on data it has never seen.** That is what Autopilot does.

**Who it is for:** the on-call ML engineer or ML platform team running tabular models in
production (credit, fraud, pricing, churn), at the moment a quality alert fires or a
nightly retrain fails.

## How it works

1. **Measure.** Splits are frozen and hashed before anything is fitted. The baseline is rebuilt
   and findings are computed (metric drop, drift, missingness, pipeline errors, segment
   spread). Each finding carries its method and sample count.
2. **Hypothesise.** If and only if the measurements show an incident, **Nemotron 3 Ultra**
   reads the measured values, the incident report and the team's notes (free text a rules
   engine cannot use) and proposes 2–4 competing causes, citing real finding IDs.
3. **Test cheaply.** Every approved test is scored by expected information gain ÷ cost. The
   UI shows why the chosen test beats a retrain. Tests run in killable child processes.
4. **Research when needed.** A library error with no local answer triggers a sanitised
   **Tavily** search (official docs first) and extract. Only an official source can
   justify a fix, and the installed library must confirm the parameter it names.
5. **Fix narrowly.** Once one cause is supported, the smallest matching fix is built: an
   input repair, a threshold, a sourced parameter rename. Retraining is never the default.
6. **Verify.** A separate verifier service checks the artifact, protocol parity, leakage,
   sample size and metric provenance, opens the final test set **once**, and requires a
   paired-bootstrap confidence interval plus a critical-segment guardrail.
7. **Decide.** The receipt shows the cause, the fix, the held-out result and the spend.
   **Nemotron 3.5 Lightning** writes a summary for a manager; any number in it that is
   not in the verified facts gets it discarded. Approval is recorded; no live model is changed.

"Rejected" and "not enough evidence" count as correct answers.

## How NVIDIA Nemotron and Nebius Token Factory are used

| Role | Model | Why this size | Guardrail |
|---|---|---|---|
| Plan competing causes | `nvidia/Nemotron-3-Ultra-550b-a55b` | Reasoning over measurements plus messy human context | Schema-checked JSON; every cited finding/source must exist; one corrective retry with the exact error; honest fallback to rules |
| Manager summary | `nvidia/Nemotron-3_5-Lightning` | Short, cheap, fast | Every number must appear in the verified facts or the summary is discarded |

- Each call is shown in the UI with model, provider, latency, tokens and estimated cost
  at Nebius list prices. A typical investigation costs about **$0.003**.
- The models never run code, choose tests or report metrics. The selector, policy gate,
  runner and verifier are deterministic code.
- A deterministic gate decides whether there is an incident at all. A model asked to
  explain a healthy model will still name causes (we measured this), so it is not asked.

## Results

**Equal-budget benchmark** (offline, rules planner, held-out seeds 100–104,
[report](docs/benchmarks/autopilot_benchmark.json)):

| Approach | Correct decisions | False promotions | Cost units | Retrains |
|---|---|---|---|---|
| Retrain first (classic response) | 22/40 | 0 | 300 | 30 |
| Fixed-order checklist | 35/40 | 0 | 143 | 0 |
| Random eligible test | 36/40 | 0 | 171.5 | 0 |
| **Autopilot** | **40/40** | **0** | **92.5** | **0** |

Verifier must-fail controls (leaked feature, wrong split, segment regression): **15/15
rejected**.

**Live ablation** (real Nemotron and Tavily calls, same seeds and budget,
[report](docs/benchmarks/autopilot_ablation_live.json)):

| Planner | Correct | False promotions | Median model time | Est. model cost (40 runs) |
|---|---|---|---|---|
| Rules only | 40/40 | 0 | – | – |
| Nemotron 3 Ultra (forced, no fallback) | 37/40 (37/39 excluding 1 provider outage) | 0 | 15 s | $0.10 |

| Tavily, library-upgrade incident (T01) | Decision |
|---|---|
| On | 5/5 **verified sourced fix** |
| Off | 5/5 diagnosis only: without a cited official source the policy refuses to guess a rename |

What this says: on incidents whose detectors we wrote, the rules planner is as accurate or
more. Nemotron's misses were a healthy model on one seed and a threshold incident on two.
The verifier caught every one of them: there are **zero false promotions in either arm**.
Nemotron earns its place by reading the free-text incident context and explaining the
causes in plain language, and it is safe to use because a deterministic verifier decides.
A rule anchor added a cause Nemotron left out in 8 of 40 runs, and the UI labels it.

Two fixes came out of this ablation. The first live run scored Nemotron 31/40 and T01
3/5: Nemotron invented causes for a healthy model, and the sourced fix also re-tuned the
threshold. Now a deterministic gate decides whether there is an incident at all, and the
fix changes only the renamed parameter.

**Read these numbers honestly.** The incidents are synthetic and we wrote both the faults
and the detectors. Only the seeds are held out. The benchmark shows the selection
policy beats simple baselines on these fault classes under an equal budget. It does
not show performance on real production incidents.

## Run it

```bash
git clone https://github.com/sankalp2515/AutoML-Autopilot.git && cd AutoML-Autopilot
cp .env.example .env     # set POSTGRES_PASSWORD, AUTOPILOT_VERIFIER_TOKEN, a Nemotron key, TAVILY_API_KEY
docker compose up -d --build
```

Open <http://localhost:3000>. Pick an incident, press **Start investigation**, and watch
the stages. S02 (unit bug), T01 (library upgrade, live Tavily) and S10 (a challenger that
hurts rural customers) are good first runs.

## Architecture

Five containers on one private network; only `web` publishes a port
([details](docs/DEPLOYMENT.md)).

```
 browser ──► web (Next.js) ──/api──► api (FastAPI control plane) ──┐
                                                                    ├──► postgres (state, queue, leases)
                worker (execution plane) ───────────────────────────┘
                  │  ├──HTTP + token──► verifier (no internet, read-only artifacts, no LLM)
                  │  └──► artifacts volume
                  └──egress──► Nebius Token Factory · Tavily
```

| Plane | Service | May | May not |
|---|---|---|---|
| Experience | `web` | Show evidence, receipts, approvals | Hold any secret |
| Control | `api` | Validate, scope per visitor, rate-limit, queue | Execute investigations, hold provider keys |
| Execution | `worker` | Claim by lease, run vetted templates, call Nemotron/Tavily | Read final-test rows, change a live model |
| Verification | `verifier` | Read the frozen split, issue verdicts | Reach the internet, write artifacts, call a model |
| State | `postgres` | Store investigations, append-only events, leases | Be reached from outside |

**Reliability, tested in containers:** killing the worker mid-investigation leaves the run
`FAILED / E_INTERRUPTED` when its lease expires (about 60 s). It is never resumed half-way
and never reported as a success. Claims are compare-and-swap, so two workers can never run
the same investigation.

**Trust records:** every item is labelled *observed* (measured), *retrieved* (Tavily) or
*inferred* (model or rule). Every experiment is admitted as an envelope (dataset, split and
evaluation-protocol digests, template version, parameters, expected outcomes, cited
sources, resource ceiling) whose digest the verifier checks before it runs.

## Tests

```bash
cd backend && pip install -r requirements-runtime.txt pytest pytest-asyncio
python -m pytest tests/test_autopilot -q        # 96 tests, no keys needed
```

They cover every incident against the hidden oracle, the information-gain maths, the
policy gate, runner timeouts, the verifier gates, Tavily contracts (injection, outdated
and third-party sources, provider errors, privacy gate), Nemotron contracts (invalid
citations, corrective retry, provider outages), summary grounding, worker leases and
fail-safe recovery, per-visitor isolation and the verifier service's authentication.

## Honest limits

- Incidents are seeded synthetic loan data. Uploading your own incident pack is not built.
- Belief weights are an uncalibrated heuristic. Information gain is exact only given them.
- Experiments run as vetted templates in killable processes, not in a gVisor/microVM sandbox.
- Tabular binary classification with scikit-learn only.
- Approval records a decision; there is deliberately no path that changes a live model.

## What was built during the hackathon

AutoML Autopilot is built on our earlier **AutoML Orchestrator** codebase. From it we reuse
the FastAPI app, auth and tenancy, the SQLAlchemy setup, logging and the frontend design
system. The orchestrator's 10-agent training pipeline is still in `backend/app/agents`, but
this deployment does not run it.

Written during the submission period:
- `backend/app/autopilot/`: engine, profiler, planners, selector, policy gate, runner,
  verifier and verifier service, research, worker, guards, pricing, benchmark and ablation.
- `backend/tests/test_autopilot/`, `frontend/app/autopilot/`,
  `frontend/components/autopilot/`, `frontend/lib/autopilot.ts`.
- The Docker deployment (`docker-compose.yml`, `backend/Dockerfile.autopilot`,
  `frontend/Dockerfile.prod`).
- The docs in `docs/`.

## Docs

- [Product and API guide](docs/AUTOPILOT.md)
- [Deployment, trust boundaries and reliability](docs/DEPLOYMENT.md)
- [Architecture decision v4.1](docs/ARCHITECTURE_DECISION_v4_1.md)
- [Implementation plan](docs/AUTOPILOT_PLAN.md)

## License

GPL-3.0. See [LICENSE](LICENSE).
