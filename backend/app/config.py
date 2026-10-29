from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "AutoML Orchestrator"
    DEBUG: bool = False
    SECRET_KEY: str = "change-this-in-production"

    DATABASE_URL: str = "postgresql+asyncpg://automl:automl@postgres:5432/automl"
    DATABASE_URL_SYNC: str = "postgresql://automl:automl@postgres:5432/automl"

    REDIS_URL: str = "redis://redis:6379/0"

    MLFLOW_TRACKING_URI: str = "http://mlflow:5000"

    LLM_PROVIDER: str = "gemini"  # legacy — chain below takes precedence
    # Fallback chain: tried left-to-right; providers without keys are skipped.
    LLM_FALLBACK_CHAIN: str = "gemini,openrouter,deepseek,groq,ollama"

    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.1-flash-lite"
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "nvidia/nemotron-3.5-content-safety:free"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"
    OLLAMA_URL: str = "http://host.docker.internal:11434"
    OLLAMA_MODEL: str = "llama3.1"
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

    SANDBOX_URL: str = "http://sandbox:8001"

    DATA_DIR: str = "/data"

    MAX_ITERATIONS: int = 3
    IMPROVEMENT_THRESHOLD: float = 0.02
    SANDBOX_TIMEOUT: int = 300
    # Wall-clock budget for a whole pipeline run (seconds). 0 = no cap. Guards
    # against a run that hangs on external retries/loops (R6).
    MAX_RUN_SECONDS: int = 7200
    # Wall-clock budget per agent node (seconds). 0 = no cap. A timeout becomes
    # a clean failed status (same path as _safe_run), never a hang or crash.
    MAX_AGENT_SECONDS: int = 1800
    # Hard guard: reject absurdly wide CSVs (column count). 0 = no cap.
    MAX_DATASET_COLUMNS: int = 2000
    # Global training-row cap: train.csv is (stratified-)sampled down to this
    # at the data_splitter, so EVERY downstream agent (baseline/selector/tuner/
    # evaluator) trains on at most this many rows. 0 = no cap. The holdout is
    # never sampled — the final score stays an honest full-holdout estimate.
    MAX_TRAIN_ROWS: int = 200_000
    # Above this many rows the scale strategy switches to STREAMING (out-of-core
    # partial_fit over every row) instead of sampling. Configurable so the streaming
    # path can be exercised on a smaller-but-real dataset in test/CI.
    STREAMING_THRESHOLD_ROWS: int = 2_000_000
    # S1 adaptive cap: when capping, the splitter fits a fast probe on 25/50/100%
    # of the capped sample. If the learning curve is still climbing above the
    # noise floor at 100%, the cap escalates (up to this hard ceiling) so
    # rare-pattern datasets keep the rows they need. 0 = never escalate.
    MAX_TRAIN_ROWS_HARD: int = 500_000
    # Comma-separated allowed CORS origins (production should set this explicitly).
    CORS_ALLOW_ORIGINS: str = "http://localhost:3000,http://localhost:3002,http://frontend:3000"

    # Security (P16) — opt-in. When API_KEY is set, mutating requests require the
    # X-API-Key header. Rate limiting applies per-IP via Redis when enabled.
    API_KEY: str = ""
    RATE_LIMIT_PER_MIN: int = 0   # 0 = disabled

    # MLOps: scheduled drift monitoring (seconds between checks; 0 = off).
    # When an active deployment's live traffic drifts, ONE challenger re-run is
    # auto-launched (24h cooldown); the champion stays deployed until promoted.
    DRIFT_MONITOR_INTERVAL_S: int = 0

    # LLM cost/quality controls (AI-engineering hardening).
    LLM_CACHE_ENABLED: bool = True        # cache identical (system,user,model,temp) completions
    LLM_CACHE_TTL_S: int = 3600
    # Parallelize candidate cross-validation across CONCURRENT sandbox calls
    # (asyncio, one process per sandbox request) — this is what the main pipeline
    # actually uses; it is NOT Ray. Renamed from the misleading USE_RAY (which
    # implied a Ray cluster). USE_RAY below still gates the real Ray path used by
    # the research toolkit's parallel_map.
    PARALLEL_CANDIDATES: bool = True
    USE_RAY: bool = True                  # actual Ray backend for parallel_map (research toolkit only)
    # S3 seam: attach to an existing Ray cluster ("ray://head:10001" or
    # "auto"). Empty = local mode. When set, parallel_map joins the cluster —
    # the same pipeline code scales from one box to many with no code change.
    RAY_ADDRESS: str = ""
    # S3 cloud-data seam: when set, datasets can be CONNECTIONS (s3://bucket/key)
    # instead of uploads — the auditor's loader fetches them. Empty = local files
    # only. Full cloud burst (ECR images + EC2/EKS Ray workers) is deployment
    # config, not app code; see docs/ROADMAP_scale_dl_discovery.md (S3).
    DATA_S3_BUCKET: str = ""
    AWS_REGION: str = ""
    DL_ENABLED: bool = True               # set true after rebuilding the sandbox with torch (DL-0) so the scout can pick TorchMLP
    # DL-2c: prefer MiniLM sentence embeddings over TF-IDF for free-text columns.
    # Requires the sandbox image built with TEXT_EMBED=1 (bakes in the model);
    # falls back to TF-IDF automatically when the model isn't present.
    TEXT_EMBED_ENABLED: bool = False
    TENANT_BUDGET_USD: float = 0.0         # cumulative LLM-cost cap per tenant (0 = unlimited)
    INJECTION_GUARD_STRICT: bool = True   # reject goals with detected prompt-injection (else log+strip)

    # Multi-tenancy (Phase 4) — opt-in. JSON mapping of api-key → tenant id, e.g.
    # '{"key_abc":"acme","key_xyz":"globex"}'. EMPTY = single-tenant "public" mode
    # (no behavior change). When set, each run is owned by the caller's tenant and
    # cross-tenant access is denied. Per-tenant cap on concurrently active runs
    # (0 = unlimited). USE_JOB_QUEUE routes runs through a durable arq worker
    # instead of an in-process background task (survives API restarts).
    # Master switch for ALL auth/tenant enforcement. Default OFF so the app runs
    # open by default — having Supabase keys in .env (needed by the frontend) does
    # NOT turn on the backend auth wall. Set AUTH_ENABLED=true to require auth.
    AUTH_ENABLED: bool = False
    TENANT_API_KEYS: str = ""
    QUOTA_MAX_ACTIVE_RUNS_PER_TENANT: int = 0
    USE_JOB_QUEUE: bool = False

    # Supabase auth (Phase 5) — opt-in. Set SUPABASE_JWT_SECRET (Project Settings →
    # API → JWT Secret) to accept `Authorization: Bearer <supabase access token>`;
    # the authenticated user's id becomes their tenant ("user:<uuid>"). The URL +
    # anon key are for the frontend client. Empty = Supabase auth disabled.
    SUPABASE_JWT_SECRET: str = ""
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_JWT_AUD: str = "authenticated"

    # ── AutoML Autopilot (evidence-directed failure diagnosis) ──────────────
    # Nebius Token Factory planner (NVIDIA Nemotron). Empty key = planner runs in
    # deterministic mode and every receipt says so; there is no silent fallback
    # to another vendor's model.
    NEBIUS_API_KEY: str = ""
    NEBIUS_BASE_URL: str = "https://api.tokenfactory.nebius.com/v1/"
    NEBIUS_PLANNER_MODEL: str = "nvidia/Nemotron-3-Ultra-550b-a55b"
    # Small model for the stakeholder summary (cheap, fast; output is number-checked).
    NEBIUS_FAST_MODEL: str = "nvidia/Nemotron-3_5-Lightning"
    NEBIUS_TIMEOUT_S: float = 45.0
    # NVIDIA API catalog (build.nvidia.com), the same Nemotron family over an
    # OpenAI-compatible endpoint. Used when NEBIUS_API_KEY is empty. Nebius wins
    # when both are set, so the hackathon path stays Token Factory once it works.
    NVIDIA_API_KEY: str = ""
    NVIDIA_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_PLANNER_MODEL: str = "nvidia/nemotron-3-ultra-550b-a55b"
    NVIDIA_FAST_MODEL: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    NVIDIA_TIMEOUT_S: float = 90.0
    # Nemotron 3 reasons before answering by default (40-80 s per plan). The planner
    # output is schema-checked JSON, so the trace adds latency without adding trust.
    NVIDIA_ENABLE_THINKING: bool = False
    # v5 repair agent tiers. Ultra reasons about hypotheses; the coder writes edits; the fast
    # tier is reserved for compaction. NVIDIA's catalog does not serve Super to this account,
    # so the coder falls back to Ultra there. Verify Nebius ids when the key arrives (K9).
    NEBIUS_CODER_MODEL: str = "nvidia/Nemotron-3-Super-120b-a12b"
    NVIDIA_CODER_MODEL: str = "nvidia/nemotron-3-ultra-550b-a55b"
    # v5 services. Empty URLs mean "in-process" (development); production sets both.
    REPAIR_SANDBOX_URL: str = ""             # sandbox-runner service (only component with Docker access)
    REPAIR_SANDBOX_TOKEN: str = ""
    REPAIR_VERIFIER_URL: str = ""            # verifier service v5 (only component with labels)
    REPAIR_VERIFIER_TOKEN: str = ""
    REPAIR_RUNNER: str = "docker"            # docker | process (process = unisolated, tests/dev only)
    REPAIR_ENV_ROOT: str = ""                # where environments live; must be the same path on the Docker host
    REPAIR_FIXTURES_DIR: str = ""            # bundled sample repositories (api + verifier only)
    # Access and spend (BE-12). Live runs are off unless enabled; replays always work.
    REPAIR_LIVE_ENABLED: bool = False
    REPAIR_INVITE_CODES: str = ""            # comma-separated; empty = no code needed when live is enabled
    REPAIR_DAILY_USD_CAP: float = 5.0
    REPAIR_TASK_USD_CEILING: float = 0.40    # worst-case estimate reserved per task against the daily cap
    REPAIR_DAILY_TASKS_PER_TENANT: int = 5
    REPAIR_MAX_REPO_MB: int = 50
    REPAIR_MAX_FILES: int = 5000
    REPAIR_ALLOWED_GIT_HOSTS: str = "github.com,gitlab.com,bitbucket.org"
    REPAIR_RETENTION_HOURS: int = 72
    REPAIR_PIP_INDEX_URL: str = ""           # optional allow-listed package index for installs
    REPAIR_VERIFIER_STATE_DIR: str = ""      # verifier-only writable state (ledger, admissions, caches)
    REPAIR_PACKS_DIR: str = ""               # holdout label packs: api writes, verifier reads, worker never mounts
    REPAIR_REPLAYS_DIR: str = ""             # bundled recorded runs (docs/benchmarks/repair)
    # Tavily runtime research. Empty key = research state UNAVAILABLE.
    TAVILY_API_KEY: str = ""
    TAVILY_BASE_URL: str = "https://api.tavily.com"
    TAVILY_TIMEOUT_S: float = 20.0
    AUTOPILOT_MAX_SEARCHES_PER_RUN: int = 2
    AUTOPILOT_MAX_EXTRACTS_PER_RUN: int = 3
    # Per-experiment wall clock (seconds) enforced by killing the child process.
    AUTOPILOT_EXPERIMENT_TIMEOUT_S: int = 120
    # "process" (isolated, killable child) or "inline" (tests / benchmark speed).
    AUTOPILOT_RUNNER: str = "process"
    # Where investigations execute: "inline" = inside the API process (dev, tests);
    # "worker" = the API only queues, a separate worker service claims runs by lease.
    AUTOPILOT_EXECUTION: str = "inline"
    AUTOPILOT_LEASE_S: int = 60                # worker lease; renewed every LEASE_S/3 while running
    AUTOPILOT_WORKER_CONCURRENCY: int = 2
    # Separate verifier service (empty = verify in a local child process).
    AUTOPILOT_VERIFIER_URL: str = ""
    AUTOPILOT_VERIFIER_TOKEN: str = ""
    AUTOPILOT_VERIFIER_TIMEOUT_S: int = 180
    # Public-demo guards (see autopilot/guard.py).
    AUTOPILOT_CREATES_PER_10MIN: int = 6       # per browser session / IP
    AUTOPILOT_DAILY_LLM_CALLS: int = 400       # Nemotron calls per UTC day, all visitors
    AUTOPILOT_DAILY_TAVILY_CALLS: int = 200    # Tavily calls per UTC day, all visitors
    AUTOPILOT_IDLE_EXPIRY_MIN: int = 15        # step-mode runs untouched this long are cancelled
    AUTOPILOT_SHARE_SECRET: str = ""          # otherwise persisted in DATA_DIR/.share-key
    AUTOPILOT_ALERT_TOKEN: str = ""           # disabled until configured
    AUTOPILOT_UPLOAD_TTL_HOURS: int = 24

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
