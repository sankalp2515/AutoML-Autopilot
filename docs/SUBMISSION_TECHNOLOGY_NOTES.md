# Submission notes: substantial update and Nebius feedback

## Significant update to the existing project

AutoML Autopilot v5 extends the earlier AutoML/data-investigation project into repository-native ML software repair. The new workflow snapshots a repository, enforces an editable-code contract, investigates causes, generates source changes and regression tests, executes candidates in isolated workspaces, and independently grades both software behavior and declared ML invariants. A tenant-scoped task/event system, durable verifier admission ledger, patch/PR handoff and provenance-preserving replay experience support review. Nothing merges automatically.

The hosted hackathon version adds real Nemotron inference through Nebius Token Factory and a direct Nebius Sandboxes runtime adapter. Google Cloud hosts the application/control plane. It does not host the Nemotron model. Receipts identify the actual provider, model, token usage and immutable sandbox image UUID. Historical NVIDIA developer-endpoint recordings remain labeled separately.

Use the pinned submission commit to establish the precise change boundary. Do not claim all pre-existing AutoML functionality was newly built for this event. The earlier orchestrator and v4 incident workflow are retained and documented in `LEGACY_SCOPE.md`.

## Feedback based on genuine use

These are draft submission answers, not feedback already sent to Nebius.

- Both Nemotron 3 Super and Nano accepted genuine calls through Token Factory. The OpenAI-compatible interface let us reuse the bounded inference ledger and usage reporting. Tiny probes returned in approximately 1.17 s and 0.84 s respectively; these two calls are not general latency benchmarks.
- Sandboxes' immutable saved-image UUIDs made it practical to fork candidates and reuse a prepared dependency environment without sharing writable repository state. We retained the real UUID in evidence instead of pretending it was a Docker image content digest.
- Explicitly disabling networking on each execution, removing inherited environment variables and separating trusted evaluation uploads helped implement the evidence boundary. We tested these capabilities with genuine sandbox executions.
- We used the sandbox project identifier with the Token Factory credential and the documented Sandboxes API base. Making the API host/project-header distinction prominent in quick-start examples would make integration easier.
- Repository repair depends on repeated command execution and file transfer. A documented end-to-end example covering saved images, repeated bounded commands, regular-file downloads, timeouts/cancellation and image retention would be useful beyond a single disposable execution example.
- The beta's restriction on personal/sensitive data and provider image retention materially affect product design. We disclose these restrictions and use public/synthetic fixtures. UUID metadata backups cannot substitute for long-term image exports.
- Model tool-action reliability still matters even when JSON mode succeeds. One genuine credit fixture returned structurally invalid tool actions and honestly ended INCONCLUSIVE. This is a product/model integration observation, not evidence of an endpoint outage or a successful repair.

Supporting artifacts and limits are linked in [deployment validation](NEBIUS_DEPLOYMENT_VALIDATION.md). Do not present the incomplete historical comparison as a measured superiority claim.
