# NVIDIA evaluation execution report — 30 October 2026

This is a local NVIDIA developer-endpoint evaluation, not Nebius Token Factory
execution. The user authorized six pilots and a 72-slot comparison with four
independently supplied credential slots. Credential values are never evidence.

## Observations

All six full-agent pilots completed: churn-service, loan-scorer and refund-risk
were VERIFIED; credit-approval, fraud-batch and pricing-model were INCONCLUSIVE
after provider failures. Refund-risk did not produce the required genuine leakage
rejection. A successful repair is not evidence of that rejection behavior.

The comparison saved 15 of 72 planned slots: three externally VERIFIED and twelve
INCONCLUSIVE. Those three verified slots were churn-service/A/7, loan-scorer/A/7
and loan-scorer/D/7. No ranking or full-study success-rate claim is justified.
All saved failures remain in the denominator of the partial summary.

Repeated ReadTimeouts were followed by HTTP 503. Independent short Ultra requests
on each of the four supplied keys also returned HTTP 503. The cause is not
established; no shared-quota claim is made. An earlier D-ML slot returned HTTP 400.
The comparison was stopped during loan-scorer/17/D-TAV, which has no completed
result and is listed separately from the 15 completed slots. Earlier setup/restart
interruptions also occurred; this was not one uninterrupted execution.

## Protocol and artifacts

- `nvidia-pilot/`: six reports/results and a partial-arm summary.
- `nvidia-comparison/`: fixed 6 × 3 × 4 plan, 15 reports/results and incomplete summary.
- `nvidia-availability.json`: account-level probes, including later HTTP 503 results.
- `execution-status.json`: machine-readable blocker and provenance limitations.
- `comparison-plan/`: offline planning artifact, never counted as model execution.

All three requested model tiers used the explicitly selected Ultra endpoint
`nvidia/nemotron-3-ultra-550b-a55b`: the tested Super and Nano model IDs returned
404. All arms use the same model mapping, admitted task limits and requested seeds;
sampling reproducibility is provider dependent. Full uses two candidate branches;
generic uses one branch with the same aggregate tool/token ceilings. The Docker
runner is isolated; a separate verifier owns grading. A prewarmed, pinned-dependency
base image avoided redundant network installation; per-snapshot preparation and
runtime isolation still apply. Thinking was disabled, with 120-second HTTP timeout.

The generic and no-ML arms decide acceptance from software checks. Their accepted
patches are independently ML-graded afterward, without feeding that result back
to the agent. Full arms reuse their final receipt and do not reopen the holdout.
Report, plan and repository digests are retained in each result.

The first harness version recorded actual external verdicts for churn-service/A/7
and loan-scorer/A/7 but omitted the full external ML receipt from their report JSON.
Their external decisions are retained with this limitation; neither is published
as a receipt-complete replay. Later runs persist that receipt. Original reports
were not modified to invent the missing artifacts.

Failed or timed-out calls may have unknown provider usage. Summaries therefore
leave aggregate costs unknown when call accounting is incomplete. Recorded dollar
amounts are Nebius-rate equivalents, not NVIDIA invoices. No paid-provider switch
was made. The public benchmark excludes the incomplete comparison.

## Resume the historical study only

This study is frozen to commit `b8d6f94` and prompt `repair-v4`. Use an isolated checkout of that commit for the commands below. The current engine uses `repair-v5`, trusted verifier registration and immutable image admission, and deliberately refuses to mix configurations in an existing study directory. Start a new directory for current-engine measurements. The October 30 backend-audit planner and Tavily pair are separate studies; provider availability has since recovered, so the old outage is historical, not a current blanket blocker.


Use the same configured isolated runner, independent verifier, NVIDIA model mapping,
seeds and plan directory. The operator bootstrap used local credentials in memory
outside the repository; the reusable harness does not load dotenv.

```powershell
python scripts/evaluate_repair.py run --allow-model-calls --max-runs 72 --out docs/benchmarks/repair/nvidia-comparison
python scripts/evaluate_repair.py summarize --out docs/benchmarks/repair/nvidia-comparison
```

Existing result slots, including failures, are skipped rather than overwritten.
Only 57 slots remain in this study. Retrying failed completed slots requires a new
study directory and a disclosed retry policy. Confirm short endpoint requests
succeed before resuming. Publish with `publish_repair_evidence.py --comparison`
only after a complete, reviewed study; the publisher refuses incomplete summaries.
