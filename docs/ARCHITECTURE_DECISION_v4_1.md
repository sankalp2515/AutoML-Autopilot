# Architecture decision: v4.0 review → final v4.1

Date: 2026-10-28, about 40 hours before the deadline. Inputs: ChatGPT's *Architecture Spec v4.0* and
its two diagrams, the code as it exists in this repo, and our internal review against the judging criteria.

> **Update (2026-10-28, later):** the deployment moved to five containers (web, api, worker,
> verifier, postgres) on a private network, with lease-based worker dispatch and a separate
> verifier service. That pulls the v4.0 "durable dispatch" and "separate verifier write
> authority" items into the build. See `DEPLOYMENT.md`.

## Decision

**Keep v4.0 as the target architecture and its vocabulary. Do not adopt its P0 build list.**
The final design is **v4.1**: v4.0's concepts mapped onto what is already built and working, plus
five scoring gaps v4.0 doesn't cover. Most of v4.0's new infrastructure moves to P1/P2.

Why v4.0's P0 list doesn't work for the deadline:

1. **It spends the remaining hours where judges won't look.** v4.0's P0 adds a Postgres outbox
   with compare-and-swap transitions, worker leases and a reconciler, a Redis/arq queue, MinIO, a
   separate evaluator service, MLflow wiring and container workers. That is roughly 40+ hours of
   infrastructure. Judges score by *using* the demo for two minutes. Their own session criticised
   "mega-monoliths" and reliability work that crowds out the product experience.
2. **It is hard to host.** The demo must stay free, unrestricted and up until 15 Dec. A 6–9 service
   stack costs more, has more failure points, and is harder to keep alive than our current
   backend + frontend + SQLite.
3. **It misses the gaps that most affect the score.** v4.0 says "Nemotron proposes" but never says
   *why a language model is needed*. It has no model tiering (Ultra / Super / Nano), which the Best
   Apps track text asks for explicitly. It also has no answer to "isn't this Datadog Bits for ML?",
   no real-data incident, no demo concurrency or spend controls, and no hosting plan. These cost
   more points than durable queues gain.
4. **It widens scope.** "Dataset + ML objective" as an input, plus serving, canary and rollback
   boxes, pull the product back toward generic AutoML/MLOps. The judges reward one job done well.
   Drawing boxes we haven't built is also an honesty risk: judges click, and an ML researcher on the
   panel will check.

What v4.0 gets right, and we keep:

- **The planes and authority table** (experience / control / reasoning / execution / evidence /
  verification): who may do what. This is the clearest way to explain the trust model, so use it
  in the README.
- **The three linked records**: hypothesis ledger → experiment envelope → verification receipt.
- **Provenance classes** `OBSERVED` / `RETRIEVED` / `INFERRED` on every evidence item. This is
  cheap and visibly rigorous.
- **Honest information-gain wording.** IG is exact only given uncalibrated weights; never present
  it as Bayesian proof. We already label weights "uncalibrated"; tighten the copy.
- **Ablations as P0 evidence**: Nemotron-off vs on and Tavily-off vs on under the same budget, with
  losing results reported.
- **The "kill the worker, fail safe" scenario** as a reliability proof.
- **ADRs 001 and 004–008** (modular monolith, evaluator-only result writes, no arbitrary code,
  provenance categories, promotion off by default, no statistical claim from uncalibrated IG).
  These match what is built.

## v4.0 component by component: built, cheap to add, or defer

| v4.0 component | In the repo today | v4.1 decision |
|---|---|---|
| Hypothesis ledger with prediction/observation revisions | **Built**: hypotheses, typed predictions per test, `belief_history`, per-test consistency verdicts | Keep. Add a belief chart (Design). |
| Diagnostic selector (discrimination × utility / cost) | **Built**: IG ÷ cost, with rankings, reasons and denials logged before execution | Keep. Rename the UI metric "expected discrimination (bits, given uncalibrated weights)". |
| Proof-Carrying Experiment Envelope | **Mostly built**: `policy.signature` is a canonical hash of inputs digest + protocol hash + template + params + code version; outputs are SHA-256'd | **P0, ~1 h**: emit an explicit envelope object (dataset manifest digest, split/eval protocol digest, expected buckets, evidence refs, resource ceiling, `authorized_by`) and digest it *excluding* the digest field. Show it in the receipt. |
| Policy gate and allow-list | **Built**: unknown template, extra fields, bounds, budget, duplicates, unsourced renames | Keep. |
| Isolated workers | **Built (lite)**: killable child process per experiment, vetted templates only, timeout | Keep for P0. Containerised, no-network workers → P1. |
| Independent verifier | **Built**: 8 gates, single-use final test, paired bootstrap CI, segment guardrail | **P0, ~2 h**: run the verifier in its own child process that alone reads final-test labels; the runner never receives them. This matches v4.0's "separate write authority" without a new service. |
| Provenance classes | **Partial**: findings vs evidence vs hypotheses are separate types | **P0, ~1 h**: tag and badge every item `OBSERVED` / `RETRIEVED` / `INFERRED`. |
| Durable state (Postgres outbox, CAS, leases, reconciler) | **Partial**: append-only events, illegal-transition guard, restart recovery marks orphans FAILED (never success) | P0 = **demonstrate** fail-safe recovery (kill the server mid-run, show FAILED + `E_INTERRUPTED`, incumbent unchanged). Outbox, leases and resume → P1. |
| Redis/arq queue, MinIO object store | Not used by Autopilot | P1. The SQLite + local artifact store is enough for the demo. |
| MLflow registry, serving, canary, rollback | Exists in the old orchestrator, not wired to Autopilot | P2. Approval stays **record-only** (ADR-007). Do not draw these as built. |
| OIDC/RBAC, org isolation | Supabase auth exists; Autopilot runs public | P1. P0 needs **per-browser anonymous sessions** instead (see below). |
| Nebius Token Factory | Adapter built; currently runs through the NVIDIA API | **P0 blocker**: eligibility requires Nebius. |
| Tavily Search + Extract + firewall | **Built and live**: sanitiser, official-first, source gate, `CONTEXT_ONLY`, installed-signature check | Keep. Widen beyond one incident (P1). |

## What v4.1 adds that v4.0 lacks (all P0)

1. **Nemotron with a job only a language model can do.** Pass finding *values* plus unstructured
   incident context (the on-call message, deploy notes, release notes, the challenger's model card).
   Rules can't read those. Today the planner sees only finding titles, which is why it missed the
   units bug on S02.
2. **Model tiering with receipts.** Ultra plans the hypotheses; Super or Nano extracts claims from
   model cards and writes the stakeholder summary. Each call shows model, tokens, latency and cost,
   with a total per run.
3. **Demo safety for many judges at once.** One anonymous session per browser (not one shared
   tenant), auto-expiry of abandoned step-mode runs, a visible queue position instead of HTTP 429,
   per-IP rate limits and daily spend caps on Nebius and Tavily.
4. **Hosting that lasts until 15 Dec.** Two services (API with SQLite, Next.js), ideally on Nebius
   (Serverless Endpoint or a small AI Cloud VM), with keys server-side.
5. **Market position on the landing page and in the README.** "The AI SRE for model quality,
   with a verifier that can say no." Plus one incident on a real public dataset.

## Final v4.1 P0 build list (in order)

| # | Item | Hours | Source |
|---|---|---|---|
| 1 | Nebius Token Factory live; judged runs name Nebius in every receipt | 1 (after the key works) | Gap review |
| 2 | Per-browser sessions, run expiry, queue position, rate and spend caps | 3 | Gap review |
| 3 | Nemotron context (values + incident text) and Ultra/Super-or-Nano tiering with cost receipts | 4 | Gap review |
| 4 | Explicit experiment envelope + `OBSERVED/RETRIEVED/INFERRED` badges | 2 | v4.0 |
| 5 | Verifier in its own process with sole final-test access | 2 | v4.0 |
| 6 | Ablation table: rules vs Nemotron vs hybrid, and Tavily on vs off (T01 class) | 3 | v4.0 + gap review |
| 7 | One real public-data incident, run live | 4 | Gap review |
| 8 | Fail-safe demo: kill mid-run, show FAILED, incumbent untouched | 0.5 | v4.0 |
| 9 | Slim hosted deploy with spend caps, smoke-tested from another device | 4 | Gap review |
| 10 | README + architecture diagram v4.1 (only built boxes solid; P1/P2 dashed and labelled) + repo cleanup + licence fix | 4 | Both |
| 11 | Video script, feedback form, "significantly updated" statement | 3 | Both |

Total is about 30 hours, which leaves a buffer inside the ~40 remaining. The last 6 hours are
reserved for a fresh-device run-through, a secrets scan and submission. No new features then.

## Diagram changes for v4.1

- Keep v4.0's six bands. **Solid** boxes only for what runs today. **Dashed** with "P1/P2 target" for
  outbox/leases/queue, MinIO, container sandbox, OIDC, registry/serving/canary/rollback.
- In the reasoning band, show **Nemotron Ultra (plan)** and **Nemotron Super/Nano (extract,
  summarise)** as separate boxes, both via Nebius Token Factory.
- Add a small "demo edge" box: per-session tenant, rate limit, spend cap.
- Rename the input "Dataset + ML objective" to "**Model incident**: alert, failed retrain, or
  challenger", to keep the product to one job.

## Risks this decision accepts

- No exactly-once or resume-after-crash. We claim only fail-safe recovery, which is true and tested.
- Same-host isolation between runner and verifier (separate processes, not separate machines). The
  README states the weaker assurance plainly, as v4.0 itself recommends.
- If judges weigh production durability heavily, v4.0's P1 items are our stated roadmap, not
  shipped features.
