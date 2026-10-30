# v5 local operations and production boundaries

The tested topology has eight services: web, API, legacy worker, repair worker, verifier, sandbox-runner, queue PostgreSQL and verifier-only PostgreSQL. Hosting, TLS/domain setup and real Nebius validation remain owner work.

## Start without model credentials

From the repository root, with Python and Linux Docker available:

```powershell
python scripts/v5_stack.py up --project automl-v5-local --port 3133
python scripts/v5_stack.py doctor --project automl-v5-local --port 3133
python scripts/v5_stack.py warm --project automl-v5-local --port 3133
```

Open http://localhost:3133/repair. Bundled genuine recordings work without inference. `up` builds two images, creates/migrates both databases, sanitizes the API sample catalog and waits for all health checks. First image builds, registry downloads and six ML dependency environments depend on connection/host speed; an under-ten-minute clean-host guarantee has not been established. `warm` prepares Python 3.11/3.12 and each sample twice, reporting content image IDs and measured times.

The helper reads no `.env` and disables all provider keys unless `--allow-live` is supplied. Explicit live mode consumes credentials already present in the process environment. Internal random credentials persist in the operator state directory, outside the checkout. Keep that directory private and back it up separately in an access-controlled credential store. Windows users should restrict the directory ACL to their account; POSIX mode bits alone are insufficient on Windows. Do not print Docker environment inspection or the operator JSON in support logs.

## Trust and routing

- Web publishes a loopback port. API, queue and workers share a private backplane; workers have provider egress. API egress supports authorized repository fetch/OAuth/delivery.
- Only verifier joins the internal verification network containing the separate ledger database. Queue and ledger app roles are non-superusers on fresh installations.
- Only sandbox-runner has the Docker socket. This is a trusted host-control capability; use a dedicated host/rootless daemon or a separately validated stronger runtime for hostile multi-tenant use.
- API sees sample source only. Sealed bundles live on the verifier. Packs are written by API and read by verifier, never mounted in worker or candidate containers.
- Grading, task registration and trusted evaluation use three distinct service tokens. A worker grading token cannot register a contract or launch a trusted evaluator.
- Runtime containers have no network, UID 10001, read-only base, bounded tmpfs/CPU/RAM/PIDs, dropped capabilities and no-new-privileges. Installer networking is allowed only during preparation, through the operator's credential-free HTTPS index.
- Exact top-level dependency pins and wheels are mandatory. Transitive packages are not fully hashlocked. The admitted immutable image digest binds actual resolved dependencies for all subsequent branches/checkers.
- One sandbox service owns one environment root/node. Root locking prevents two owners; restart reaps only namespace-labelled orphan containers. Scaling APIs/workers against transactional PostgreSQL is supported; transparent multi-host sandbox routing is not implemented.

## Maintenance and retention

Task creation has tenant-scoped, payload-bound `Idempotency-Key` support. Admission serializes global estimated dollar/token commitments in the queue database. Missing provider accounting retains the reservation; estimates are not provider invoice guarantees. Cancellation and stale fencing revoke worker mutation authority. Unregistered, queued and leased jobs expire rather than silently verifying after a crash. Worker health continues independently of long model calls.

API retains clones/uploads/snapshots/packs by configured TTL, preserving unfinished task references. Sandbox separately expires idle environments and its own image tags, with environment and execution-count limits; there is no global Docker prune. Verifier expires reproducible cache files, never admissions/holdout permits. Owner delete APIs cover staged repositories and packs, rejecting active references. Archived reports/replays are audit evidence and are not silently erased by cache retention.

**Hard total disk quotas are not implemented by these application limits.** They require host/project filesystem quotas or an equivalent runtime storage limit. The host target is awaiting the owner's choice. Monitor free disk and keep public custom-repository live access gated until that control is configured. This is a remaining BE-20 production acceptance item.

## Consistent backup and restore

Wait for warm-up/discovery/operator jobs to finish. Finish or cancel active v4/v5 tasks first. The helper pauses admission, stops HTTP ingress, refuses a snapshot with active tasks, stops application writers, dumps both PostgreSQL databases and archives artifacts/packs/verifier state. Each member is SHA256 checked. Originals restart and admission resumes even on a normal backup exception.

```powershell
python scripts/v5_stack.py seed-drill --project automl-v5-local --port 3133
python scripts/v5_stack.py backup --project automl-v5-local --port 3133 --directory C:/private-backups/v5-001
python scripts/v5_stack.py restore --project automl-v5-restored --port 3134 --directory C:/private-backups/v5-001
```

Use a new backup directory and a different empty restore project. Existing target containers/data volumes, wrong checksums, archive links/traversal and unexpected members are refused. Restore uses original local image IDs, migrates, starts and compares task counts, database receipt hashes, embedded replay receipt hashes, replay bytes and verifier-ledger fingerprint. Preserve/export those images for off-host recovery; registry/image retention is an operator responsibility. The drill seed imports an actual historical NVIDIA replay, plus explicit diagnostic DB/ledger markers; it executes no new repair and is not a new benchmark result. `down` keeps volumes; never use `down -v` for a retained system.

Backups contain private repository data and labels. Keep them on encrypted storage with restrictive ACLs, separate from public evidence. A public object-store backup policy, off-site recovery and scheduled backup alerts depend on the hosting target.

## Existing installations and secret rotation

Fresh DB init creates least-privilege application roles. Existing Docker PostgreSQL volumes do not rerun init scripts; the helper's doctor deliberately rejects superuser application roles. Back up first, then have the database owner create non-superuser replacement roles, grant only the application DB/schema privileges, move object ownership as needed and rotate application DSNs. Never automatically alter the existing owner's running stack.

Rotate one service credential pair at a time during a maintenance window: API admission token with verifier registration token; worker grading token with verifier grading token; worker/API sandbox token with sandbox service; trusted evaluator token with verifier/sandbox. Update operator state through a private editor/credential manager, recreate affected services, then run doctor and a sample repair. DB password rotation additionally requires `ALTER ROLE` by the DB owner; changing environment text alone does not rotate an initialized database password. Provider keys belong only in worker processes. Redaction tests and repository scans reduce accidental disclosure; they do not certify all possible third-party logs.

## Frozen interface proposals

`contracts.py` and `sandbox/base.py` were preserved. Current implementations use optional platform capabilities: `environment_image`, `prepare_pinned`, `trusted_run`, inventory and cleanup. A future protocol revision should explicitly version immutable-image preparation and trusted artifact evaluation, with separate authorization/capability interfaces. Legacy sealed pytest that imports candidate Python is refused in production; repository owners must supply the supported inert artifact-export protocol. These are deliberate compatibility boundaries, not arbitrary Python proof of correctness.
