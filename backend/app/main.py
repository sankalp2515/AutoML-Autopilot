from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.artifacts import router as artifacts_router
from app.autopilot.routes import router as autopilot_router
from app.autopilot.sharing import install_access_log_filter, redact_path
import app.autopilot.models  # noqa: F401  (registers Autopilot tables for create_all)
from app.api.routes.config import router as config_router
from app.api.routes.discovery import router as discovery_router
from app.api.routes.registry import router as registry_router
from app.api.routes.health import router as health_router
from app.api.routes.extras import router as extras_router
from app.api.routes.inference import router as inference_router
from app.api.routes.observability import router as observability_router
from app.api.routes.runs import router as runs_router
from app.api.websocket import router as ws_router
from app.config import settings
from app.core.logging import setup_logging
from app.database import init_db
from app.redis_client import close_redis


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    install_access_log_filter()
    # Apply the runtime config overlay (Settings control panel) over env defaults.
    from app.core import runtime_config
    runtime_config.apply_overlay()
    # Production guard: refuse to start silently with the placeholder secret.
    if not settings.DEBUG and settings.SECRET_KEY == "change-this-in-production":
        from app.core.logging import get_logger
        get_logger("startup").warning(
            "insecure_default_secret_key",
            detail="SECRET_KEY is the default placeholder — set a real value in production.",
        )
    await init_db()
    try:
        from app.autopilot.service import recover_orphans
        await recover_orphans()
    except Exception as _e:  # never block startup on recovery
        from app.core.logging import get_logger
        get_logger("startup").warning("autopilot_orphan_recovery_failed", error=str(_e)[:200])
    # Recover runs orphaned by a restart: in-process pipelines don't survive a
    # restart, so a previously-"running" run would otherwise hang forever. Mark
    # them failed (and queued ones too in in-process mode) so the user can Retry.
    try:
        from sqlalchemy import update
        from app.database import AsyncSessionLocal
        from app.models.run import Run
        _msg = "Interrupted by a server restart — click Retry to run it again."
        async with AsyncSessionLocal() as _db:
            await _db.execute(update(Run).where(Run.status == "running").values(status="failed", error_message=_msg))
            if not settings.USE_JOB_QUEUE:
                await _db.execute(update(Run).where(Run.status == "queued").values(status="failed", error_message=_msg))
            await _db.commit()
    except Exception as _e:  # never block startup on recovery
        from app.core.logging import get_logger
        get_logger("startup").warning("orphan_recovery_failed", error=str(_e)[:200])
    # MLOps: scheduled drift monitoring → auto-challenger (opt-in, see config).
    import asyncio as _asyncio
    from app.core.drift_monitor import drift_monitor_loop
    _drift_task = _asyncio.create_task(drift_monitor_loop())
    yield
    _drift_task.cancel()
    await close_redis()


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ALLOW_ORIGINS.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request observability (Layer 1 + correlation id for Layer 5) ──────────────
import re as _re
import time as _time
import uuid as _uuid

from app.core import context as _ctx
from app.core import metrics as _metrics
from app.core.logging import get_logger as _get_logger

_req_log = _get_logger("http")
# Collapse ids in the path so the metric label doesn't explode in cardinality.
_ID_RE = _re.compile(
    r"/(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|\d+)(?=/|$)"
)


def _norm_path(p: str) -> str:
    return _ID_RE.sub("/:id", redact_path(p))


@app.middleware("http")
async def request_observability(request, call_next):
    """Assign/propagate an X-Request-ID, time the request, log it, and record
    HTTP metrics. The request id is bound to a contextvar so every downstream log
    (LLM calls, decisions) can be correlated to the originating request."""
    request_id = request.headers.get("x-request-id") or _uuid.uuid4().hex[:16]
    _ctx.set_request_id(request_id)
    t0 = _time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        dur = _time.perf_counter() - t0
        path = _norm_path(request.url.path)
        try:
            _metrics.http_requests_total.labels(
                method=request.method, path=path, status=str(status)).inc()
            _metrics.http_request_duration_seconds.labels(
                method=request.method, path=path).observe(dur)
        except Exception:
            pass
        _req_log.info("http_request", request_id=request_id, method=request.method,
                      path=redact_path(request.url.path),
                      status=status, latency_ms=round(dur * 1000, 1))


# ── P16: opt-in API-key + rate limiting (no-op unless configured) ─────────────
_OPEN_PATHS = ("/health", "/metrics", "/docs", "/redoc", "/openapi.json")


@app.middleware("http")
async def security_gate(request, call_next):
    from starlette.responses import JSONResponse
    path = request.url.path
    is_open = any(path.startswith(p) for p in _OPEN_PATHS)

    # API key on mutating methods when a key is configured
    if settings.API_KEY and not is_open and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.headers.get("x-api-key") != settings.API_KEY:
            return JSONResponse({"detail": "Invalid or missing X-API-Key"}, status_code=401)

    # Per-IP rate limit via Redis sliding-minute window (fail-open on Redis error)
    if settings.RATE_LIMIT_PER_MIN and not is_open:
        try:
            from app.redis_client import get_redis
            import time
            r = await get_redis()
            key = f"rl:{request.client.host}:{int(time.time() // 60)}"
            n = await r.incr(key)
            if n == 1:
                await r.expire(key, 60)
            if n > settings.RATE_LIMIT_PER_MIN:
                return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        except Exception:
            pass

    return await call_next(request)

# Prometheus instrumentation MUST attach at import time — middleware cannot be
# added once the app has started (which is what the lifespan does). Previously
# this lived in the lifespan and only "worked" because the package was missing
# and the ImportError was swallowed; with it baked into the image the bug surfaced.
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    Instrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/health"],
    ).instrument(app).expose(app, endpoint="/metrics_fastapi", include_in_schema=False)
except ImportError:
    pass

# Phase 4: one guard enforces tenant ownership on EVERY run-scoped endpoint
# (no per-endpoint hole). No-op in single-tenant "public" mode.
from app.core.auth import enforce_run_ownership
from fastapi import Depends

_owned = [Depends(enforce_run_ownership)]

app.include_router(health_router)
app.include_router(config_router)
app.include_router(discovery_router)
app.include_router(registry_router)


@app.get("/api/v1/studios", tags=["studios"])
async def list_studios_endpoint():
    """Platform seam: the studios (applications) this deployment offers."""
    from app.core.studios import list_studios
    return {"studios": list_studios()}
app.include_router(runs_router, dependencies=_owned)
app.include_router(artifacts_router, dependencies=_owned)
app.include_router(observability_router, dependencies=_owned)
app.include_router(inference_router, dependencies=_owned)
app.include_router(extras_router, dependencies=_owned)
app.include_router(ws_router)
# AutoML Autopilot: tenant scoping is enforced inside each route (path param is
# investigation_id, not run_id, so the Run-ownership guard does not apply).
app.include_router(autopilot_router)


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus scrape endpoint."""
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
    except ImportError:
        return Response(content="# prometheus_client not installed\n", media_type="text/plain")


@app.get("/")
async def root() -> dict:
    return {"service": settings.APP_NAME, "version": "1.0.0", "docs": "/docs"}
