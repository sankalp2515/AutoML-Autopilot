import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.run import Run

router = APIRouter(prefix="/api/v1/runs", tags=["artifacts"])

ARTIFACT_MAP = {
    "notebook":  "pipeline.ipynb",
    "model":     "tuned_model.pkl",
    "pipeline":  "inference_pipeline.pkl",
    "preprocessor": "preprocessor.pkl",
    "model_card": "model_card.md",
    "api":       "api_main.py",
    "eda_report": "eda_report.html",
    "shap":      "shap_summary.png",
    "confusion_matrix": "confusion_matrix.png",
    "target_dist": "target_distribution.png",
    "correlation": "correlation_heatmap.png",
}


@router.get("/{run_id}/artifacts")
async def list_artifacts(run_id: str, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    artifact_dir = Path(settings.DATA_DIR) / run_id / "artifacts"
    if not artifact_dir.exists():
        return JSONResponse({"run_id": run_id, "artifacts": []})

    files = []
    for f in sorted(artifact_dir.iterdir()):
        if f.is_file():
            # Find friendly name
            friendly = next((k for k, v in ARTIFACT_MAP.items() if v == f.name), f.name)
            files.append({
                "name": friendly,
                "filename": f.name,
                "size_kb": round(f.stat().st_size / 1024, 1),
                "download_url": f"/api/v1/runs/{run_id}/artifacts/{friendly}",
            })

    return JSONResponse({"run_id": run_id, "artifacts": files})


@router.get("/{run_id}/artifacts/{artifact_name}")
async def download_artifact(
    run_id: str,
    artifact_name: str,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    filename = ARTIFACT_MAP.get(artifact_name, artifact_name)
    file_path = Path(settings.DATA_DIR) / run_id / "artifacts" / filename

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Artifact '{artifact_name}' not found. Run may still be in progress or this artifact was not generated."
        )

    media_types = {
        ".ipynb": "application/json",
        ".pkl":   "application/octet-stream",
        ".md":    "text/markdown",
        ".py":    "text/plain",
        ".html":  "text/html",
        ".png":   "image/png",
        ".csv":   "text/csv",
        ".json":  "application/json",
    }
    media_type = media_types.get(file_path.suffix, "application/octet-stream")

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=media_type,
    )
