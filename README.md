# AutoML Autopilot

**The ML coding agent that checks beyond green tests.**

[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)
[Product docs](docs/API_V5.md) · [Architecture](docs/V5_ARCHITECTURE.md) · [Evidence](docs/benchmarks/repair/LIVE_EVALUATION.md) · [Contributing](CONTRIBUTING.md) · [Support](SUPPORT.md)

AutoML Autopilot repairs Python ML repositories and produces a patch with independent software and ML verification. It is for engineers debugging broken serving code, preprocessing changes, leakage, serialization failures and quality regressions. Passing a test suite is useful evidence; it is not enough to prove a model still behaves correctly.

The agent investigates competing causes, writes source edits and regression tests in isolated candidate workspaces, and submits the result to a separate verifier. The reviewer sees the diff, checks, missing evidence and verdict before downloading a patch or requesting a draft PR. **Nothing merges automatically.**

## See it in two minutes

1. Start the local stack below and open `/repair`.
2. Choose **Watch a verified repair**. This is a labelled genuine recording, not a new inference call. No account or model key is required to watch.
3. Open the candidate diff, then **Verdict & receipt**. Inspect the code and ML checks, their recorded runtime and provenance.
4. Return to the landing page and inspect the rejected churn example. Software checks passed while the online/batch comparison failed. That historical recording used an unisolated test runner and says so.

The landing page also links authored leakage and protected-group checker proofs. These are reproducible validation cases, not customer testimonials or autonomous success-rate claims.

## Local start

Prerequisites: Python 3.12+, Docker CLI/Compose v2 and a running Linux Docker daemon. Internet is needed for first image/dependency downloads. Windows can use Docker Desktop with Linux containers.

```powershell
python scripts/v5_stack.py up --project automl-v5-local --port 3133
python scripts/v5_stack.py doctor --project automl-v5-local --port 3133
```

Open **http://localhost:3133/repair**. This starts eight services, initializes/migrates PostgreSQL, prepares a source-only sample catalog and mounts recorded evidence. No user `.env` is read or written. Internal generated credentials live in a private operator directory outside the checkout. Model keys are cleared and live inference is disabled by default.

Optional fixture warm-up and shutdown:

```powershell
python scripts/v5_stack.py warm --project automl-v5-local --port 3133
python scripts/v5_stack.py down --project automl-v5-local --port 3133
```

Shutdown preserves data volumes. Do not use `down -v` on a retained installation. Fresh-host setup time depends on registry/network speed; no under-ten-minute guarantee is claimed. See [operations](docs/V5_OPERATIONS.md) for credentials, backup/restore, least-privilege roles and remaining host requirements.

## Run a new repair

Live work is explicit, bounded and disabled unless enabled. An operator supplies provider credentials through a private process environment, configures invite access and starts with `--allow-live`. The [judge-only plan](docs/JUDGE_DEPLOYMENT_AND_COSTS.md) explains the $29.50 balance, lifetime allowance, cooldown, replay access and hosting choices. Do not paste credentials into issues, receipts or Git history.

In **New repair**, choose a sample, public repository URL or ZIP; review `autopilot.yaml`; select failing tests and ML requirements; optionally upload evaluation labels separately; then start. Six included repositories cover churn parity, loan preprocessing, refund leakage, credit group recall, fraud serialization and pricing numeric boundaries. Without labels, the product reports limited label-free evaluation rather than inventing predictive quality.

### A minimal contract

```yaml
version: 1
name: python-service
python: "3.12"
install: pip install -r requirements.txt
commands:
  test: python -m pytest -q tests
task:
  type: restore
  fail_to_pass:
    - tests/test_service.py::test_service
  ml_invariants: []
policy:
  allowed_edit_roots: [src/, tests/]
  immutable_existing_tests: true
```

This software-only example does **not** establish model quality. ML tasks also declare train/predict commands, data paths, serving/load entrypoints and the desired invariants. See the [six sample contracts](fixtures/repos) and in-app contract documentation at `/repair/docs`. Installs require exact top-level dependency pins and wheels from the operator-approved HTTPS index. Existing tests and protected files remain immutable; added regression tests are allowed by policy.

## What is running, and where

![As-built v5 architecture](docs/assets/v5-architecture.svg)

- **Frontend and API:** Next.js workspace and FastAPI control plane; tenant-scoped tasks, contracts, packs, events and delivery.
- **Repair worker:** Nemotron hypothesis/planning and candidate generation; bounded calls, tools, source edits and checkpoints.
- **Execution boundary:** tested Docker CLI sandbox service; network disabled during runs, immutable prepared images and independent workspaces.
- **Verifier:** separate authority for code/ML receipts, sealed data and a separate transactional PostgreSQL admission/holdout ledger.
- **Storage:** application PostgreSQL plus artifact/pack/verifier volumes. Backups and replay receipt hashes were checked in an empty-target restore drill.

**Nebius status:** the inference client is OpenAI-compatible with Token Factory. Current genuine evidence is from NVIDIA developer endpoints. A direct Nebius Sandboxes adapter is **not implemented**; it is the next runtime integration, not a key-only configuration change. No Nebius validation is claimed yet. [As-built versus target](docs/V5_ARCHITECTURE.md).

## How Nemotron and Tavily are used

Nemotron proposes explanations and edits; it cannot issue the final trusted verdict. The intended Token Factory mapping is Ultra for diagnosis, Super for patching and Nano/Lightning for fast work. The current client records actual provider/model/usage per call. Missing accounting stays unknown.

Tavily is conditional: relevant library/API failures trigger official-documentation retrieval. Sources and excerpts remain attributed external data. One matched current-engine B01 pair with Tavily enabled/disabled independently verified both patches; the enabled arm actually retrieved official documentation. That one pair demonstrates integration, not a general benefit estimate.

## Results and honest limits

| Evidence | What it establishes |
|---|---|
| Six fixture proof stories | Unpatched failure, an authored wrong-green rejection and reference acceptance under the declared protocol |
| Genuine NVIDIA recordings | Specific model-generated repair outcomes with provider/runtime provenance |
| Six planner measurements | Five precise leading mechanisms, one partial diagnosis; single seed, reference-aware manual review |
| Partial historical four-arm study | Six pilots and 15/72 comparison slots; insufficient for a comparative marketing headline |
| Local backend audit | 398 passing backend tests, two skips at commit 530fe9f; Docker boundary and restore evidence |

[Evidence artifacts](docs/benchmarks/repair) · [Backend audit](docs/BACKEND_OPERATIONS_AUDIT.md) · [Current backlog](docs/V5_BACKLOG_FINAL.md)

A VERIFIED receipt means the declared checks passed for the admitted snapshot, patch and evaluation data. It is not a guarantee over all future inputs. Legacy sealed tests importing candidate Python are unsupported in production; use the clean inert-artifact protocol. Arbitrary frameworks, multi-host sandbox routing, hard host disk quotas and public production deployment remain outside proven acceptance. Nebius beta forbids personal/sensitive data; use public code and synthetic inputs for that demonstration.

## Develop and verify

```powershell
# Backend, from backend/
python -m pip install -r requirements-runtime.txt pytest pytest-asyncio
python -m pytest tests/test_autopilot tests/test_repair -q -m "not slow and not docker"
python -m pytest tests/test_repair/test_sandbox_local.py -q
python -m pytest tests/test_repair -q -m slow
# Frontend, from frontend/
npm ci --legacy-peer-deps
npx tsc --noEmit
npm run build
```

PostgreSQL tests require disposable URLs through `REPAIR_TEST_POSTGRES_URL` and `REPAIR_TEST_LEDGER_URL`; their tables are recreated. Never point them at application data. Browser verification and required checks are described in [CONTRIBUTING.md](CONTRIBUTING.md). Model calls are not required for the deterministic test suite.

## Earlier modes and license

The earlier data-incident mode remains reachable at `/autopilot`. The original dataset-to-model orchestrator source is retained for reproducibility and future extraction; it is not the v5 repair engine or a supported dependency of the hackathon product. See [legacy scope and maintenance policy](docs/LEGACY_SCOPE.md) and the [historical v4 README](docs/LEGACY_V4_README.md).

Repository code is licensed under [GNU GPL version 3](LICENSE). Existing third-party code/data retains its own notices; the pinned public MLOps subset includes upstream licenses and provenance under [fixtures/external](fixtures/external). NVIDIA models, Nebius and Tavily are separate services with their own terms. Their use does not imply endorsement, partnership or a hackathon award.
