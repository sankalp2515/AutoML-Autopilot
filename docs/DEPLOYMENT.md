# Deployment

AutoML Autopilot ships as five containers on one private Docker network. One command
starts everything; only the web container publishes a port.

```bash
cp .env.example .env          # then set the values marked REQUIRED
docker compose up -d --build
docker compose ps             # all five should become "healthy"
open http://localhost:3000    # redirects to /autopilot
```

## Services

| Service | Image / command | Role | Networks | Data | Secrets it receives |
|---|---|---|---|---|---|
| `web` | `frontend/Dockerfile.prod` · `node server.js` | Next.js standalone server. Proxies `/api/*` to `api` on the private network | edge (published `:3000`), backplane | none (read-only FS) | none |
| `api` | `backend/Dockerfile.autopilot` · `uvicorn app.main:app` | Control plane: validation, per-visitor sessions, rate limits, queueing, receipts. **Executes no investigations** (`AUTOPILOT_EXECUTION=worker`) | backplane | none (read-only FS) | DB password |
| `worker` | same image · `python -m app.autopilot.worker` | Execution plane: claims queued investigations by lease, runs the engine, experiments in killable child processes, calls Nemotron and Tavily | backplane, egress | `artifacts` volume (rw) | DB password, provider keys, verifier token |
| `verifier` | same image · `uvicorn app.autopilot.verifier_service:app` | Verification plane: the only service that reads final-test rows and writes verification records. No language model | backplane only (**no internet**) | `artifacts` volume (**read-only**) | verifier token |
| `postgres` | `postgres:16-alpine` | System of record: investigations, append-only events, leases, worker heartbeats | backplane | `pgdata` volume | DB password |

`backplane` is an `internal: true` network: nothing on it can reach the internet, and
nothing outside can reach it except through `web`.

## Trust boundaries

```
 browser ──HTTPS──► web ──/api──► api ─┐
                                       ├──► postgres  (state, queue, leases, heartbeats)
                    worker ────────────┘
                      │  ├──HTTP + token──► verifier ──read-only──► artifacts
                      │  └──rw──────────────────────────────────► artifacts
                      └──egress──► Nebius Token Factory / NVIDIA API / Tavily
```

- Provider keys exist only in the `worker` container. The API reports provider status
  from the worker's heartbeat, so it never holds a key.
- The verifier accepts a run id (UUID), never a path. It checks a shared token with a
  constant-time compare, refuses candidate artifacts outside that run's workspace, and
  has no route to the internet.
- Every container: `no-new-privileges`, all Linux capabilities dropped (Postgres adds
  back the five its entrypoint needs), CPU/memory/PID limits, rotated JSON logs,
  `restart: unless-stopped`. App containers run as uid 10001 on a read-only root
  filesystem with a `tmpfs` for `/tmp`.

## Reliability

| Failure | What happens |
|---|---|
| Worker crashes or is killed mid-investigation | Its lease (60 s, renewed every 20 s) expires. The next worker tick marks the investigation `FAILED / E_INTERRUPTED` with the incumbent unchanged. It is never resumed half-way and never reported as a success. |
| Worker crashes before starting a run | The run is still `CREATED`; another worker claims it when the lease expires. |
| Two workers race for the same run | Claims are compare-and-swap on `(lease_owner, lease_until)`; exactly one wins. |
| Verifier unavailable | The investigation ends `FAILED / E_VERIFIER_UNAVAILABLE`. No verdict is reported without the verifier. |
| Provider outage or rate limit | One retry on 429/5xx/timeouts. If the model is still unusable, the run falls back to the rules planner and says so. Tavily outage → research `UNAVAILABLE`. |
| Daily provider budget reached | New runs use the rules planner and skip Tavily, and the UI says why. |
| Postgres restart | Services reconnect. Postgres health gates the startup order. |
| `docker compose stop` | The worker gets 60 s to finish what it holds (`stop_grace_period`). |

Delivery is at-least-once with idempotent claiming. There is exactly one accepted
terminal result per investigation, because only the lease holder writes its state.

## Operations

```bash
docker compose logs -f worker                 # structured JSON logs
docker compose exec api python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/api/v1/autopilot/health').read())"
docker compose up -d --scale worker=2         # more workers; leases keep them apart
docker compose down                           # keep data
docker compose down -v                        # delete data volumes too
```

Health: `GET /api/v1/autopilot/health` returns `{database, execution, worker_online}`.
Provider status and today's provider usage: `GET /api/v1/autopilot/providers`.

## Configuration

See `.env.example`. Required: `POSTGRES_PASSWORD`, `AUTOPILOT_VERIFIER_TOKEN` and at least
one model key (`NEBIUS_API_KEY` for the judged path; `NVIDIA_API_KEY` works the same way).
Optional: `TAVILY_API_KEY`, `WEB_PORT`, `PUBLIC_ORIGIN`, `AUTOPILOT_WORKER_CONCURRENCY`,
and the demo guards (`AUTOPILOT_CREATES_PER_10MIN`, `AUTOPILOT_DAILY_LLM_CALLS`,
`AUTOPILOT_DAILY_TAVILY_CALLS`).

## Hosting on one VM

Any Docker host with 2 vCPU / 4 GB is enough for the demo (for example a small Nebius
AI Cloud CPU VM). Put a TLS-terminating reverse proxy (Caddy or nginx) in front of
`web:3000` and set `PUBLIC_ORIGIN` to the public URL.

## Not in this deployment (stated, not hidden)

- **No OS-level sandbox for experiments.** They are vetted templates in killable child
  processes inside the worker container. A gVisor/microVM runner is roadmap.
- **No secrets manager.** `.env` and compose environment only.
- **No TLS inside the stack.** Traffic on the private network is plain HTTP; terminate
  TLS at the edge proxy.
- **Single Postgres, no replicas or backups** configured by this file.
