# v5 architecture: as built and deployment target

![As-built architecture with status legend](assets/v5-architecture.svg)

The diagram preserves the target's developer-control row, source/control/delivery columns, infrastructure row and cross-cutting controls. Teal means implemented and tested; amber means integration or external verification remains; violet means roadmap. Read status per box, not as an assertion of cloud deployment.

## Runtime boundaries

| Zone | Owns | Must not own |
|---|---|---|
| Browser / Next.js | Workspace UX, replay playback, review controls | Model keys, sealed labels, trusted verdict authority |
| API | Tenant permissions, intake, contract, global admission, trusted verifier registration | Model calls, candidate execution or candidate-authored verdicts |
| Repair worker | Hypotheses, tools, source changes, bounded provider calls, fenced checkpoints | Holdout labels, admission token, trusted evaluation capability, Docker socket |
| Sandbox service | Candidate environments; local Docker daemon or hosted Nebius control credential | Guest access to credentials; verifier packs |
| Independent verifier | Sealed bundles/packs, clean artifact test evaluator, code/ML receipts | Provider keys; agent control over final verdict |
| Verifier PostgreSQL | Durable admission and single-finalist holdout permit | Worker/API database access |

The application PostgreSQL persists tasks/events/candidates/receipts/worker heartbeats and atomic admission commitments. A separate verifier database binds the original ECC, snapshot bytes, immutable dependency image and sealed bundle. A changed diff cannot reuse the same logical candidate permit. Reproducible caches expire; verifier admissions and holdout permits do not.

Candidate commands run in separate Docker containers with no network, non-root UID, read-only base, bounded tmpfs/CPU/RAM/PIDs/time/output, and only their own work/output mounts. Approved exact-pin wheel installation happens during prepare. Execution uses an immutable dependency image. The host Docker daemon and base/dependency image remain trusted. This is not a kernel-exploit guarantee.

Supported sealed artifact tests import no candidate Python; they see inert allowlisted exports in a clean independent environment. Legacy candidate-import tests remain unsupported/fail-closed. ML verification compares raw observations outside candidate authority. Tool denials and unavailable checks remain evidence rather than becoming passes.

## Deployed judge topology

A Google Cloud CPU VM now hosts Next.js, FastAPI, worker/verifier services, both PostgreSQL stores and artifact volumes. Caddy provides public HTTPS. Nemotron Super and Nano run through Nebius Token Factory; generated code runs in Nebius Sandboxes through the implemented `TokenFactoryRunner`. `RemoteRunner` still speaks our internal capability-protected sandbox API; the gateway delegates to the external adapter. Local installations retain the Docker CLI runner.

The adapter maps immutable provider image UUIDs, upload/download, independent branch state, cancellation, bounded output, network-disabled execution and clean trusted evaluation into the frozen runner capabilities. Actual boundary probes and hosted UI repairs are preserved in [deployment validation](NEBIUS_DEPLOYMENT_VALIDATION.md). The gateway holds the sandbox control key; guest execution receives no credentials. Candidate and trusted evaluator capabilities remain distinct. The immutable provider UUID is recorded as `nebius:<UUID>` rather than a fabricated image SHA.

The hosted data filesystem bounds intake, artifact and database volumes at 20 GiB in aggregate. Guest writable layers are limited to 2 GiB. Containerd's trusted service-image store remains on the boot disk; monitor both disks. Nebius provider retention is not extended by local metadata cleanup or backups.

## Scope and scaling limits

Built: one sandbox owner/node, transactional shared PostgreSQL admission/fencing, two-candidate repair, durable evidence, read-only replay and restore drill. Not established: per-tenant disk quotas, transparent multi-host sandbox routing, arbitrary framework compatibility, external OAuth/GitHub delivery, or production SLOs. Hosted HTTPS readiness and gross-cost email alerts are configured; recovery evidence is tracked in the deployment report. Model registry/serving/canary/automatic rollback in the larger vision are not v5 repair delivery features. A verified patch ends at human review.

See [operations](V5_OPERATIONS.md), [judge budget and deployment](JUDGE_DEPLOYMENT_AND_COSTS.md), [API](API_V5.md) and [legacy scope](LEGACY_SCOPE.md).
