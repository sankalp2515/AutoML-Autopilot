# Product-path evaluation, 30 October 2026 (NVIDIA developer endpoint)

**Status: incomplete; provider-limited.** No comparative headline and no success rate is claimed from this study.

## Protocol
- Driver: `scripts/evaluate_via_api.py`. It goes through the public v5 API of a running stack:
  - stages the bundled sample;
  - creates a task with the sample's own task text;
  - waits for the final status.
- Grading: nothing is graded by the driver. Every decision comes from the stack's independent verifier service.
  - Code checks run on the pristine snapshot.
  - ML checks run with sealed fixture labels.
  - Candidates run in the isolated `sandbox-runner` (Docker; no network, non-root).
- Arms:
  - **A:** `mode=generic`, one "make the tests pass" candidate.
  - **D:** `mode=full`, our agent with competing hypotheses, two parallel candidates and ML rules.
- Provider: `nvidia_api`. Ultra (`nvidia/nemotron-3-ultra-550b-a55b`) does planning and coding; nano is `nemotron-3.5-lightning-30b-a3b`. **No Nebius execution.**
- Stack: `scripts/v5_stack.py up --allow-live`, project `automl-ev`, built from `codex/fixtures-bug-hardening` (`cd9730b`).
- Sampling seed: not controllable through the API; provider sampling is not fixed.
- Records: each slot directory holds `result.json`, `report.json` and `events.json`. `results.jsonl` is the append-only log of every attempt, including slots that were never admitted.

## Results: completed slots only

| Fixture | A, generic | D, full agent |
|---|---|---|
| loan-scorer | **VERIFIED** (holdout ROC AUC 0.9997, 600 rows) | **VERIFIED** (holdout ROC AUC 0.9997) |
| pricing-model | **VERIFIED** (ROC AUC 0.9485 = baseline) | not admitted (twice) |
| credit-approval | REJECTED: no eligible candidate; declared failing test still failing; model produced three invalid tool replies | REJECTED: c1 still failing the declared test; c2 stopped by HTTP 429 |
| refund-risk | INCONCLUSIVE: no patch; stopped by HTTP 429 (3/8 calls failed) | REJECTED: c1 still failing the declared test; both candidates hit HTTP 429 (5/10 calls failed) |
| churn-service | not admitted (twice) | INCONCLUSIVE `SYSTEM_FAILURE/E_SANDBOX`; both candidates stopped by HTTP 429 |
| fraud-batch | not admitted (twice) | not admitted (twice) |

- **Completed:** 8 of 12 planned slots.
  - A: 4 completed (2 VERIFIED, 1 REJECTED, 1 INCONCLUSIVE).
  - D: 4 completed (1 VERIFIED, 2 REJECTED, 1 INCONCLUSIVE).
- **No incorrect patch was reported as VERIFIED.** Every VERIFIED decision is the external verifier's, with holdout evidence.

## Why the results can't be compared
1. **Rate limiting (HTTP 429)** stopped candidates in 5 of 8 completed slots. Arm D runs two candidates in parallel, so it sends roughly twice the request rate of arm A and is throttled more often. The comparison is biased against D, and no arm ranking is justified.
2. **Not admitted:**
   - Attempt 1: three requests returned HTTP 500 while the local Docker engine stalled at about 13:39 UTC, and three hit the stack's $5 daily cap.
   - Attempt 2, after the cap was raised to $50: four hit the stack's daily token allowance (2,000,000).

   The six attempt-1 placeholder results are kept in `_attempt1_not_admitted/`, and every attempt is in `results.jsonl`.
3. **Cost:** costs are `null` because calls failed partway; the provider's usage for failed calls is unknown.

## What this does and does not show
- **Shows:** the full product path works end to end on two further fixtures (loan-scorer, pricing-model). Wrong or incomplete repairs (credit-approval, refund-risk) end as REJECTED or INCONCLUSIVE, not VERIFIED.
- **Does not show:**
  - which arm is better;
  - a success rate;
  - a leakage rejection. Neither refund-risk run reached the ML leakage check, so **EV-02's leakage replay is still missing**.
- **Next:** repeat on a non-throttled provider (Nebius Token Factory) with sequential candidates, or a per-arm request-rate cap, before publishing any comparison.
