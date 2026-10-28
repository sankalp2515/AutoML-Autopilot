"""
Observability endpoints:
  GET  /runs/{run_id}/llm-stats        — per-agent LLM token/cost/latency summary
  GET  /runs/{run_id}/agent-timeline   — agent start/end with duration
  POST /runs/{run_id}/promote          — register model to MLflow Model Registry
  GET  /registry/models                — list registered models
  GET  /system/health                  — deep health check (DB, Redis, MLflow, sandbox)
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models.run import AgentStep, LLMCall, Run

router = APIRouter(prefix="/api/v1", tags=["observability"])


# ── LLM stats ──────────────────────────────────────────────────────────────────

@router.get("/runs/{run_id}/llm-stats")
async def get_llm_stats(run_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        run_check = await db.execute(select(Run).where(Run.id == run_id))
        if not run_check.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Run not found")

        rows = await db.execute(
            select(
                LLMCall.agent_name,
                func.count(LLMCall.id).label("calls"),
                func.sum(LLMCall.prompt_tokens).label("prompt_tokens"),
                func.sum(LLMCall.completion_tokens).label("completion_tokens"),
                func.sum(LLMCall.total_tokens).label("total_tokens"),
                func.avg(LLMCall.latency_ms).label("avg_latency_ms"),
                func.max(LLMCall.latency_ms).label("max_latency_ms"),
                func.sum(LLMCall.estimated_cost_usd).label("cost_usd"),
            )
            .where(LLMCall.run_id == run_id)
            .group_by(LLMCall.agent_name)
        )
        per_agent = [
            {
                "agent": r.agent_name,
                "calls": r.calls,
                "prompt_tokens": r.prompt_tokens or 0,
                "completion_tokens": r.completion_tokens or 0,
                "total_tokens": r.total_tokens or 0,
                "avg_latency_ms": round(r.avg_latency_ms or 0, 1),
                "max_latency_ms": round(r.max_latency_ms or 0, 1),
                "estimated_cost_usd": round(r.cost_usd or 0, 6),
            }
            for r in rows
        ]

        totals = {
            "total_calls": sum(a["calls"] for a in per_agent),
            "total_tokens": sum(a["total_tokens"] for a in per_agent),
            "total_cost_usd": round(sum(a["estimated_cost_usd"] for a in per_agent), 6),
            "avg_latency_ms": round(
                sum(a["avg_latency_ms"] for a in per_agent) / len(per_agent), 1
            ) if per_agent else 0,
        }

    return {"run_id": run_id, "per_agent": per_agent, "totals": totals}


# ── Agent timeline ─────────────────────────────────────────────────────────────

@router.get("/runs/{run_id}/agent-timeline")
async def get_agent_timeline(run_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        run_check = await db.execute(select(Run).where(Run.id == run_id))
        run = run_check.scalar_one_or_none()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")

        steps_q = await db.execute(
            select(AgentStep)
            .where(AgentStep.run_id == run_id)
            .order_by(AgentStep.started_at)
        )
        steps = steps_q.scalars().all()

    timeline = []
    for s in steps:
        duration_s = None
        if s.started_at and s.completed_at:
            duration_s = round((s.completed_at - s.started_at).total_seconds(), 2)
        timeline.append({
            "agent": s.agent_name,
            "status": s.status,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            "duration_s": duration_s,
            "error": s.error_message,
        })

    total_s = None
    if run.created_at and run.updated_at:
        total_s = round((run.updated_at - run.created_at).total_seconds(), 1)

    return {
        "run_id": run_id,
        "status": run.status,
        "total_duration_s": total_s,
        "timeline": timeline,
    }


# ── Model registry promotion ───────────────────────────────────────────────────

@router.post("/runs/{run_id}/promote")
async def promote_model(run_id: str, stage: str = "Staging") -> dict:
    """
    Register the tuned model from this run to the MLflow Model Registry.
    stage: "Staging" | "Production"
    """
    if stage not in ("Staging", "Production"):
        raise HTTPException(status_code=400, detail="stage must be 'Staging' or 'Production'")

    async with AsyncSessionLocal() as db:
        run_q = await db.execute(select(Run).where(Run.id == run_id))
        run = run_q.scalar_one_or_none()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.status != "completed":
            raise HTTPException(status_code=400, detail="Only completed runs can be promoted")
        if not run.mlflow_run_id:
            raise HTTPException(status_code=400, detail="No MLflow run associated")

    try:
        import mlflow
        from app.config import settings
        mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)

        model_name = f"automl-{run.winner_model or 'model'}"
        artifact_path = f"/data/{run_id}/artifacts/tuned_model.pkl"

        # Log model as MLflow artifact so it can be registered
        with mlflow.start_run(run_id=run.mlflow_run_id):
            mlflow.log_artifact(artifact_path, artifact_path="model")

        model_uri = f"runs:/{run.mlflow_run_id}/model"
        result = mlflow.register_model(model_uri=model_uri, name=model_name)

        client = mlflow.tracking.MlflowClient()
        client.transition_model_version_stage(
            name=model_name,
            version=result.version,
            stage=stage,
        )

        return {
            "registered_model": model_name,
            "version": result.version,
            "stage": stage,
            "run_id": run_id,
            "mlflow_run_id": run.mlflow_run_id,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Registry promotion failed: {e}")


# ── Registered models list ─────────────────────────────────────────────────────

@router.get("/registry/models")
async def list_registered_models() -> dict:
    try:
        import mlflow
        from app.config import settings
        mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)

        client = mlflow.tracking.MlflowClient()
        models = client.search_registered_models()
        return {
            "models": [
                {
                    "name": m.name,
                    "latest_versions": [
                        {
                            "version": v.version,
                            "stage": v.current_stage,
                            "run_id": v.run_id,
                            "created_at": v.creation_timestamp,
                        }
                        for v in m.latest_versions
                    ],
                }
                for m in models
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Deep system health ─────────────────────────────────────────────────────────

@router.get("/system/health")
async def deep_health() -> dict:
    checks = {}

    # Database
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(select(func.now()))
        checks["database"] = {"status": "ok"}
    except Exception as e:
        checks["database"] = {"status": "error", "detail": str(e)}

    # Redis
    try:
        from app.redis_client import get_redis
        r = await get_redis()
        await r.ping()
        checks["redis"] = {"status": "ok"}
    except Exception as e:
        checks["redis"] = {"status": "error", "detail": str(e)}

    # MLflow
    try:
        import httpx
        from app.config import settings
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.MLFLOW_TRACKING_URI}/health")
        checks["mlflow"] = {"status": "ok" if resp.status_code == 200 else "degraded"}
    except Exception as e:
        checks["mlflow"] = {"status": "error", "detail": str(e)}

    # Sandbox
    try:
        import httpx
        from app.config import settings
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.SANDBOX_URL}/health")
        checks["sandbox"] = {"status": "ok" if resp.status_code == 200 else "degraded"}
    except Exception as e:
        checks["sandbox"] = {"status": "error", "detail": str(e)}

    overall = "ok" if all(v["status"] == "ok" for v in checks.values()) else "degraded"
    return {
        "status": overall,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
