# Judge deployment, credit protection and cost plan

Verified against official public documentation on 2026-10-30. Balance supplied by the owner: **$29.50 Token Factory credit**. No second promo redemption is assumed. No cloud resources were provisioned and no Nebius model calls were made for this change.

## Hosting on Google Cloud meets the platform rule

The [official hackathon rules](https://nebiusglobalaihackathon.devpost.com/rules) define the Nebius requirement as a runtime call to Token Factory inference **or** running on listed Nebius AI Cloud compute. They do not mandate that the frontend/API/database be hosted on Nebius. A Google Cloud VM calling a real NVIDIA open-source model through Token Factory therefore meets this platform condition. The Coding and Agentic Engineering track asks for an agent that writes, runs and tests code in Token Factory: the proposed target uses Nebius Sandboxes for execution as well as Token Factory for reasoning. This is an interpretation grounded in the published language, not an organizer eligibility guarantee.

Other requirements remain: a functioning demo/test-build URL, public licensed source with setup instructions, a video under three minutes showing actual operation/use of the required technology, track selection, product description, feedback, and a significant-update statement for an existing project. Testing access must remain available free of charge without restriction through the judging period, which ends **December 23, 2026 at 12:00 pm Pacific** (December 24, 01:30 IST). Give judges the private access code and owner contact in Devpost testing instructions. Do not put the code in public source. Budget and concurrency protections must not leave judges unable to evaluate: reserve credit, provide an owner-assisted extension and monitor access during judging. Recordings are helpful but do not substitute for the working product claimed in the submission.

## Separate the three bills

| Component | Where | Billing basis | Can it use the $29.50? |
|---|---|---|---|
| Nemotron diagnosis/patching | Nebius Token Factory | Input/output tokens | Yes, subject to the actual grant expiry and eligible services |
| Code/train/test candidate execution | Nebius Sandboxes | Currently free while beta | No credits consumed by runs during beta |
| Website/API/workers/verifier | Google Cloud CPU VM (proposed) | Running VM time | No; separate Google Cloud billing/credit |
| Two PostgreSQL stores | Containers on the same VM | Included CPU/RAM allocation plus disk | No extra managed database service required |
| Snapshots/uploads/packs/replays | VM persistent disk | Provisioned storage/time | No; separate hosting storage |
| Tavily research | Tavily | Its own search/extract allowance | No Nebius credit; check current Tavily account allowance |
| HTTPS reverse proxy | Same VM | No additional hosted load balancer required | Included VM resources; use a certificate with no issuance fee |
| Domain | Existing domain/subdomain if available | Registrar charges if a new domain is bought | No; optional new purchase |
| Backups/egress | Private backup destination/network | Size, retention, geography and traffic | Separate hosting/backup allowance |

Sources: [Token Factory billing](https://docs.tokenfactory.nebius.com/other-capabilities/billing-new), [Sandbox beta](https://tokenfactory.nebius.com/sandboxes/about), [Google VM pricing](https://cloud.google.com/products/compute/pricing/general-purpose).

## Inference rates and credit envelope

[Official Token Factory catalog](https://tokenfactory.nebius.com/models), USD per million tokens:

| Model | Input | Output |
|---|---:|---:|
| Nemotron 3 Ultra | 1.00 | 3.00 |
| Nemotron 3 Super | 0.30 | 0.90 |
| Nemotron 3 Nano / 3.5 Lightning | 0.06 | 0.24 |

Cost = input tokens × input rate / 1,000,000 + output tokens × output rate / 1,000,000. Cached input rates differ; the planning calculations deliberately do not depend on cache hits.

Current default tier allowances: 12,000 Ultra + 160,000 coder + 20,000 fast tokens. At the more expensive output rate for every token, Ultra/Super/Lightning gives **$0.1848 per run nominally**, **$1.1088 for six**, and **$14.4144 for 78**. All tiers routed to Ultra give **$0.576 per run** and **$44.928 for 78**. Real request-level prompt overshoot, retries, accounting gaps, and changed configuration prevent these from being provider invoice guarantees.

Suggested allocation: admit at most **$20 of estimated deployment commitments**, retaining **$9.50 balance margin**. Default judge reservation is at least **$0.60 per new task** (also at least the server's nominal estimate). With no settled usage that admits 33 tasks under $20; complete known usage settles the reservation down, so more tasks can fit. Unknown/crashed accounting retains the full reservation. The lifetime cap uses all retained reservation rows, not a rolling day, and survives worker/API restarts. Do not reset the database or raise caps to work around it. External Playground/API calls are outside this ledger and reduce the same account balance: use a dedicated project/key and monitor actual provider billing.

**Conclusion:** $29.50 is a credible judge-only inference allowance for bounded validation and review; it is not a guarantee of unlimited repairs or all-Ultra 78-run evaluation. Confirm promo expiry covers judging, and stop large benchmark experiments before allocating judge access. Provider billing may automatically charge a card under its billing policy; the application ledger is not an account-level billing lock.

## Google Cloud hosting estimate through judging

Use a CPU VM, not a GPU VM. The initial recommendation is **e2-standard-4 (4 vCPU, 16 GiB), Ubuntu, 50 GiB pd-balanced**, one in-use external IPv4, no managed SQL, Kubernetes, NAT gateway or cloud load balancer. The running code still needs local Docker until the external sandbox adapter is implemented. This sizing is a starting point to test, not a production load guarantee. e2-standard-2 (2 vCPU, 8 GiB) is a lower-cost option only after measuring memory/concurrency on the complete stack. The free e2-micro is not suitable for this eight-service stack.

For a reproducible planning window use **1,320 hours (55 × 24)**, covering roughly October 30 through the end of December 23 Pacific. Add actual hours if starting earlier or retaining the VM later. Rates below are the official Iowa `us-central1` on-demand USD rates retrieved October 30, before discounts, applicable free-tier allowances and taxes.

| Charge | Unit price | Quantity / window | Calculated subtotal |
|---|---:|---:|---:|
| e2-standard-4 VM | $0.13402284/hour | 1,320 hours | $176.9101488 |
| 50 GiB pd-balanced | $0.000136986/GiB-hour | 66,000 GiB-hours | $9.041076 |
| In-use external IPv4 | $0.005/hour | 1,320 hours | $6.60 |
| **Fixed-resource subtotal** | | | **$192.5512248 (~$192.55)** |
| Network egress, off-VM backups, taxes | Destination/usage/account dependent | Not yet known | Additional; not invented as $0 |

The same disk/IP with e2-standard-2 at **$0.06701142/hour** totals **$104.0961504 (~$104.10)** before those variable charges. These are exact arithmetic under stated assumptions, not a quote for an unselected region or an exact future invoice. Choose region and verify the console estimate before provisioning. Avoid premium OS images, GPUs, unattached reserved IPv4, extra disks and managed services you do not need.

Sources: [E2 prices](https://cloud.google.com/products/compute/pricing/general-purpose#e2-machine-types), [disk prices](https://cloud.google.com/compute/disks-image-pricing), [IPv4/network prices](https://cloud.google.com/vpc/network-pricing).

Eligible new Google Cloud customers can receive **$300 for 90 days**. If sufficient unused credit remains and expires after judging, it can cover the illustrated fixed resources and a reasonable usage margin without cash payment. This is conditional on eligibility, existing balance, expiry and usage, not an assumption about the owner's account. A trial that expires/exhausts can stop resources. Existing Google Cloud billing credit is separate from the Nebius balance. [Official free-trial terms](https://docs.cloud.google.com/free/docs/free-cloud-features).

For comparison, a Nebius AMD 4-vCPU/16-GiB CPU VM plus 50-GiB network SSD costs approximately **$180.66 for the same 1,320 hours**, excluding network/backups/taxes (4×$0.015 + 16×$0.0045 per hour, disk 50×$0.071×1320/730). This also requires separate AI Cloud funding; the Token Factory balance does not establish eligibility for that bill. [Nebius compute pricing](https://docs.nebius.com/compute/resources/pricing).

## What can be removed without changing the core

- Eliminate a GPU host and dedicated LLM endpoint: use per-token Token Factory inference.
- Eliminate managed database fees: run two separate least-privilege PostgreSQL containers on one VM. Keep the separate verifier authority/store.
- Eliminate Kubernetes, managed load balancer, CDN and NAT gateway for this low-traffic single-host demo; use a reverse proxy on the VM and expose only HTTPS.
- Eliminate registry/serving/canary/billing roadmap work; v5 delivers a verified patch for human review, not production model hosting.
- Disable provider credentials in the legacy worker, unattended drift jobs and automatic CI repair triggering during judging. Keep their source and recorded incident mode.
- Keep conditional Tavily retrieval within its existing allowance; do not pay for unnecessary research on every tool call.
- Preserve independent code/ML verification, receipts, reproducible evidence, isolated execution, backups and judge usability. Removing these would weaken the product's central claim.

No hosting topology can guarantee a prize outcome. These savings retain the demonstrated idea; an underpowered/expired host or inaccessible live workflow would hurt evaluation more than the hosting brand.

## Judge-only controls and local command

```powershell
# Safe local review: no model keys and live execution remains disabled.
python scripts/v5_stack.py up --project automl-v5-judges --port 3133 --judge-only
```

Judge-only mode persists in the private operator state; later starts retain it even if the flag is omitted. To enable actual judge inference later, add `--allow-live` only with a Token Factory key and private `REPAIR_INVITE_CODES` in the process environment. That combination fails closed if either is missing and clears NVIDIA fallback keys. It also clears provider credentials from the legacy worker. The flag does not implement the Nebius sandbox adapter or provision hosting.

Preset (environment overrides are possible and must be reviewed): lifetime estimated $20, rolling daily $5, minimum reservation $0.60, 12 new runs/workspace/24 hours, 30 seconds between new starts and two shared queued/running slots. Idempotent retries return the original task before quota checks. A 429 includes Retry-After; read/replay/download/cancel are not throttled by this new admission control. Signing in does not bypass judge invite access. Empty judge-code configuration denies live starts. The UI explains the reason, current policy and recovery options before a run.

These controls preserve credit and avoid duplicate starts; they do not guarantee one judge a personal credit allocation. Shared workspace identity and budget may affect multiple judges, so supply a separate browser session or workspace per reviewer, monitor actual spending and make owner-assisted extensions available without charge. A concurrency wait is temporary; do not use limits as a paywall or silently disable evaluation before the judging period ends. Full-credit depletion cannot be fixed by UI copy: the owner must replenish or arrange a working evaluation session if necessary.

## Before any integration or hosting

Validate the local policy races on SQLite/Postgres, cumulative allowance across days/restarts, active slots, cooldown/idempotent retry, invite/sign-in bypass, 429 recovery, and unthrottled evidence reads. Build the frontend, run browser tests/axe on both themes/mobile and review screenshots. Then implement the missing Nebius sandbox adapter and run genuine Token Factory + sandbox proofs on all six supported fixtures. No public production claim until those external checks and host disk quotas pass.
