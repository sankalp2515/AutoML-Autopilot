# Grand-prize readiness review — 30 October 2026

## Assessment

The product has a credible grand-prize **idea**, with a much stronger product
experience than its earlier training-only incarnation. It is not yet a grand-prize
**evidence package**. Its strongest demonstration is specific: a coding agent can
produce a green patch that changes model behavior, and independent measurements
can reject it. That is a useful, explainable distinction from a generic coding agent.

The strongest track fit is **Coding and Agentic Engineering**: repository intake,
source editing, execution/testing, independent grading and reviewable patches are
the primary workflow. The existing data-investigation mode is secondary.

The [official criteria](https://nebiusglobalaihackathon.devpost.com/) cover technical
implementation, product design, demonstrated impact and idea quality. They also
require the eligible Nebius/NVIDIA execution path and submission artifacts. A
beautiful interface does not compensate for a missing eligibility requirement.
Provider integration, hosting and the video remain the owner's responsibilities;
this review does not add them to Codex's implementation backlog.

## Probability, with explicit assumptions

There is no defensible statistically calibrated probability: we do not know the
number/quality of eligible finished entries, reviewer behavior or their private
evaluations. Participant count is not submission count. A UI rating cannot be
converted into odds.

If a planning number is useful, my **subjective, uncalibrated estimate** is:

- **Current inspected evidence, assuming all eligibility requirements are satisfied:**
  approximately **1–5%** chance of the single grand prize.
- **After the evidence milestones below are convincingly demonstrated:** approximately
  **5–15%**, still with very low confidence. Those milestones improve the case; they
  do not make the entry a favorite against an unknown field.
- **If the project fails a mandatory eligibility requirement:** treat the chance of
  an eligible grand-prize award as **zero until that requirement is satisfied**.

These are judgment ranges for prioritization, not forecasts supported by historical
hackathon outcome data. I would not claim 50% or 80% from local tests and screenshots.

## Criteria assessment

Scores below are my internal 10-point readiness rubric, **not official scores or
weights**, and do not mathematically produce the probability range above.

| Criterion                | Current readiness | What helps                                                                                                                      | What weakens the case                                                                                                                   |
| ------------------------ | ----------------: | ------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Technical implementation |          6.5 / 10 | Real repository editing, independent code/ML receipts, fencing, isolated runner and service architecture                        | BUG-05's general sealed-test boundary remains incomplete; account-level model availability and full revised stack need runtime evidence |
| Design                   |            8 / 10 | Coherent source→contract→task journey, inspectable replays, clear rejected outcomes, light/dark/mobile and accessibility checks | Partial live telemetry, server-policy-only budgets, real-service acceptance still needed                                                |
| Potential impact         |          5.5 / 10 | Specific ML maintenance audience; catches behavior regressions tests miss                                                       | Synthetic fixtures, little independent user evidence, no matched baseline result or real-repository pilot                               |
| Idea quality             |            8 / 10 | ML-aware acceptance of code changes is a meaningful, non-obvious product thesis                                                 | Too many infrastructure claims could obscure the single clear benefit; checker trust needs precise limits                               |

## Highest-value missing evidence

1. **Matched comparison, with failures retained.** Run generic, full, no-ML and
   no-Tavily arms on the same tasks/model budgets/seeds. Use the same external
   checkers for every arm. Report verified resolution, false acceptance, denominators,
   uncertainty, latency and measured costs. Authored wrong/reference patches prove
   checker behavior, not that the autonomous agent outperforms a baseline.
2. **Breadth beyond one common synthetic template.** These five additions exercise
   meaningful gates, but share data and much scaffold. Add independently structured
   repository patterns or a small real open-source repair pilot, with provenance and
   clear task boundaries. Do not call six synthetic examples a broad benchmark.
3. **Genuine leakage and group-regression runs.** Show the actual model making a
   tempting proposal, visible tests passing, the checker rejecting it, and a valid
   finalist where available. Preserve a rejection if no finalist succeeds. A test
   script's authored patch must never be described as agent reasoning.
4. **Trust-boundary demonstration.** Demonstrate forbidden edits, denied network/host
   access, stale worker refusal, single holdout admission and fail-closed grading.
   Finish the general sealed-test protocol or explicitly limit the product to supported
   verifier-owned prediction protocols. Passing a parity fixture is not proof that
   arbitrary hostile Python cannot tamper with a harness in its own process.
5. **One complete user journey on the integrated system.** Have a new ML engineer
   pick a repository, understand the contract, inspect progress and use a receipt
   without assistance. Observe errors and confusion. Publish what was actually
   measured rather than claiming “launch-ready” from mocked browser tests.
6. **A short, concrete impact case.** State the specific failure, who owns it, why
   existing CI accepts the wrong repair, and what the reviewer learns from the
   receipt. Even a few documented user sessions are stronger than speculative
   market-size claims.
7. **One source of truth for claims.** Make the README, UI, architecture diagram and
   submission description agree about as-built versus target behavior, historical
   runner/provider provenance, unsupported frameworks and the meaning of VERIFIED.

Team invitations (P2), decorative animation and a larger number of near-identical
fixtures are lower priority than those seven evidence gaps. More features will not
automatically improve the judges' confidence in the existing core promise.

## What to lead with

“Our agent repairs ML software; an independent verifier catches patches that pass
tests but break model behavior.” Follow immediately with the measured green-but-wrong
example, then a verified patch, its source diff and its receipt. Explain which data
were checked and what remains unproven. The strongest story earns trust through a
specific observed result rather than a claim that the agent never hallucinates.

## Eligibility and the requested 90% confidence

The 1–5% planning range above already assumes eligibility. Adding a key cannot
convert it to 100% winning probability. A key is configuration; an observed eligible
NVIDIA-model execution on Nebius demonstrates the execution requirement, and other
submission/rules requirements still apply. See the [official requirements and criteria](https://nebiusglobalaihackathon.devpost.com/).

I can aim for 90% confidence that the submission is a strong, reviewable contender.
I cannot justify a 90% chance of the single grand prize without knowing the field.
Internal evidence gates that would support the former (these are not official rules):

1. A predeclared, independently structured task set across multiple fault families,
   with repeated seeds and matched model/cost budgets. Retain failures. Compare
   full, generic, no-ML and no-research arms with uncertainty and false acceptance.
2. Repairs on real repositories beyond the shared synthetic scaffold; an independent
   engineer can reproduce the rejection and verified outcome from the pinned source.
3. Observed user sessions: engineers understand the contract/receipt without coaching,
   and measured review time or defect discovery demonstrates practical value.
4. Adversarial and failure-mode acceptance of the supported verification protocol,
   clear unsupported cases, role/isolation/cancellation/restart checks, and repeatable
   clean-environment operation. No blanket “hallucination-proof” claim.
5. A coherent judge walkthrough: quickly show green-but-wrong rejection, a successful
   repair, its diff and measured receipt. Every claim matches the implementation.

Accounts, CI links and additional model types improve completeness; they do not
replace that evidence. The latest integration is documented separately in
[CODEX_API_HANDOFF_VALIDATION.md](CODEX_API_HANDOFF_VALIDATION.md).
