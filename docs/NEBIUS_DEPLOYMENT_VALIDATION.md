# Hosted Nebius validation — 2026-10-30

Public product: **https://136-112-7-18.sslip.io/repair**. Judge invite instructions are kept outside Git and must be supplied in Devpost's testing instructions. Recordings require no invite or model calls. Live runs require the private invite to preserve judging credits.

## Deployed topology

Google Cloud `e2-standard-4` in `us-central1-a` hosts Caddy, Next.js, API, repair worker, independent verifier, sandbox gateway, application PostgreSQL and verifier PostgreSQL. HTTPS terminates at Caddy; only 80/443 are public. SSH uses IAP. The VM has no attached service-account credentials. Database application roles are not superusers.

Nemotron inference uses **Nebius Token Factory**. The sandbox gateway uses **Nebius Sandboxes**, rather than the host Docker daemon, for repository code, training and tests. The gateway alone holds the sandbox control credential; guest code receives no provider credentials. Model credentials are also supplied to the repair worker. The legacy worker receives none. Candidate and trusted evaluation capabilities remain separate.

The Token Factory key and sandbox project ID were sufficient for these services. A Nebius AI Cloud console credential is not required for this Google Cloud hosting topology. Google Cloud deployment uses the owner's existing gcloud identity.

## Genuine evidence

- Super: `nvidia/nemotron-3-super-120b-a12b` — successful real inference.
- Nano: `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` — successful real inference.
- [Tiny model probes](benchmarks/repair/nebius-model-probes.json): 25 tokens per model.
- [Real sandbox boundary probes](benchmarks/repair/nebius-sandbox-probes.json): independent fork, UID 10001, disabled network, no guest credentials, killed timeout, bounded output, symlink denials, clean trusted workspace and artifact hashing.
- [Churn repair](benchmarks/repair/nebius-live/churn-service/report.json): VERIFIED on the public UI, independent code and ML checks, $0.009751 estimated inference.
- [Loan repair](benchmarks/repair/nebius-live/loan-scorer/report.json): VERIFIED on the public UI, $0.006627 estimated inference.
- [Fraud serialization repair](benchmarks/repair/nebius-live/fraud-batch/report.json): VERIFIED on the public UI, $0.021861 estimated inference.
- [Refund attempt](benchmarks/repair/nebius-live/refund-risk/report.json): REJECTED because the declared failing test remained broken. This is **not** evidence of a sealed leakage rejection; ML grading was not reached.

The credit-approval UI attempt ended **INCONCLUSIVE** (`E_NO_PATCH`) after repeated invalid model tool actions; [report](benchmarks/repair/nebius-live/credit-approval/report.json). Its expected-verdict browser assertion failed, and the failure remains recorded.

The initial churn run also verified, but its report used the generic gateway name. The subsequent run above records the concrete `tokenfactory-sandbox` identity. `nebius:<UUID>` denotes the provider's immutable image UUID, not a fabricated SHA-256 of an image. The frozen runner/contracts interfaces were preserved; a future interface revision should make the immutable image identity scheme explicit.

Six hosted attempts cost **$0.080805 estimated inference** in total, plus the two tiny model probes. Four distinct fixtures reached expected VERIFIED/REJECTED terminal verdicts through the public UI; the credit fixture did not. [Full integration ledger](benchmarks/repair/nebius-ui-validation.json).

These are small, genuine integration checks, not comparative benchmark success rates. The historical 72-slot comparison remains incomplete. Recorded NVIDIA runs retain their original provider provenance.

## Credit and availability controls

The judge deployment has a $20 cumulative **estimated** inference allowance, a $3 daily allowance, a 30-second creation cooldown, 12 tasks per workspace per day and two active repair slots. The $0.60 per-task reservation settles only when usage is known. Unknown usage retains its reservation. These controls do not guarantee the provider's final invoice and exclude calls made outside this deployment.

The public UI explains rate limits and offers recorded evidence without inference spend. The operator should monitor access and provide an extension if legitimate judging reaches a cap. Keep the invite private; ensure the Token Factory grant remains valid through judging.

Google Cloud HTTPS readiness is checked from multiple regions every five minutes. Outage alerts require failures from at least two locations. An authorized operator email channel receives outage and 50/80/100% budget notifications. The billing account uses INR: the gross-cost budget is **₹23,000**, excluding credits, through December 25. Budget alerts notify; they do not cap spend or power down the VM. This is separate from the owner's $29.50 Token Factory allowance.

## Persistence and limits

A dedicated 20-GiB filesystem bounds Docker volumes, intake files, artifacts, databases, backups and sandbox metadata. Candidate writable layers are separately limited to 2 GiB at Nebius. This is an aggregate host limit, not per-tenant filesystem quotas. Docker's containerd image store remains on the 30-GiB boot disk; monitor both disks. No host kernel-isolation claim is made for service containers.

Daily application backups quiesce admission and refuse to snapshot active tasks. Both databases, artifacts, packs and verifier state are checksummed; cloud supplements preserve private operator credentials and Nebius image metadata. A daily Google disk snapshot policy retains three days off-host. The [cloud restore drill](benchmarks/repair/nebius-cloud-restore.json) restored five genuine tasks and 13 persisted receipts with identical receipt hashes and verifier-ledger fingerprint, without new model calls. No published API replay files existed in this snapshot; bundled frontend replay bytes are source assets, not database-restore proof. Restored containers were stopped and retained privately.

Provider images remain subject to Nebius retention: backing up UUID metadata does not export those images or extend provider retention.

Nebius Sandboxes beta prohibits personal or sensitive data. Use public repositories and synthetic demonstration data. External GitHub OAuth/App delivery, independent penetration testing, multi-host routing and production SLOs are not established by these tests.

## Reproduction

`scripts/v5_cloud.py` loads root-only configuration from `/etc/automl/provider.json`. It never reads the developer's `.env` on the VM. `deploy/gcp/nebius.compose.yml` switches execution without giving the sandbox gateway a Docker socket. `scripts/configure_gcp_alerts.py` idempotently configures the Google monitoring and billing resources using the active operator identity.

`frontend/e2e/nebius-ship.spec.ts` requires explicit opt-in, a private judge-access file and the deployed URL. It exercises landing → sample → contract → live repair → terminal report, preserves genuine failures, and asserts provider/runtime/accounting provenance. Traces, screenshots and video are disabled for this test to protect the invite. Do not run a large evaluation study against the reserved judging allowance.
