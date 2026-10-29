"""HTTP API for AutoML Autopilot — mounted at /api/v1/autopilot.

Every investigation-scoped route filters by the caller's tenant (404 for other
tenants' runs). Responses never include server file paths or secrets.
"""
from __future__ import annotations

import json
import hmac
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app.autopilot import guard, service, uploads, sharing
from app.autopilot.schemas import TERMINAL, CreateInvestigation, ReviewRequest, Status
from app.core.auth import PUBLIC_TENANT, current_tenant

router = APIRouter(prefix="/api/v1/autopilot", tags=["autopilot"])
BENCHMARK_FILE = Path(__file__).resolve().parents[3] / "docs" / "benchmarks" / "autopilot_benchmark.json"
ABLATION_FILE = BENCHMARK_FILE.with_name("autopilot_ablation_live.json")


async def autopilot_tenant(tenant: str = Depends(current_tenant),
                           x_autopilot_session: str | None = Header(default=None, max_length=64)) -> str:
    """Public mode: one tenant per browser session, so visitors never share run limits."""
    return guard.scoped_tenant(tenant, PUBLIC_TENANT, x_autopilot_session)


async def _load(investigation_id: str, tenant: str) -> dict:
    st = await service.get(investigation_id, tenant)
    if st is None:
        raise HTTPException(404, "Investigation not found")
    return st


@router.get("/health")
async def health_() -> JSONResponse:
    """Liveness of the Autopilot control plane: database reachable, and in worker mode
    whether a worker heartbeat is fresh. 503 only when the database is down."""
    try:
        w = await service.latest_worker()
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "down", "database": False}, status_code=503)
    body = {"status": "ok", "database": True, "execution": service.settings.AUTOPILOT_EXECUTION}
    if service.settings.AUTOPILOT_EXECUTION == "worker":
        body["worker_online"] = bool(w) and service.worker_fresh(w)
    return JSONResponse(body)


@router.get("/scenarios")
async def scenarios_() -> dict:
    return {"scenarios": service.scenario_catalog()}


@router.get("/providers")
async def providers_() -> dict:
    used = guard.usage(await service.usage_today())
    if service.settings.AUTOPILOT_EXECUTION != "worker":
        return {**service.providers(), "demo_usage": used, "execution": "inline"}
    # The API holds no provider keys in worker mode: report what the live worker said.
    w = await service.latest_worker()
    info = (w or {}).get("info") or {}
    return {**(info.get("providers") or service.providers()), "demo_usage": used, "execution": "worker",
            "worker": {"online": bool(w) and service.worker_fresh(w), "id": (w or {}).get("worker_id")}}


@router.post("/investigations", status_code=202)
async def create_(body: CreateInvestigation, request: Request, tenant: str = Depends(autopilot_tenant),
                  idempotency_key: str | None = Header(default=None, max_length=128)) -> JSONResponse:
    if body.scenario_id.startswith("upload:"):
        uid = body.scenario_id.split(":", 1)[1]
        try:
            if not uploads.get(tenant, uid)["configured"]:
                raise HTTPException(409, "configure the upload before investigating it")
        except KeyError:
            raise HTTPException(404, "upload not found") from None
    elif body.scenario_id not in {s["scenario_id"] for s in service.scenario_catalog()}:
        raise HTTPException(422, f"unknown scenario_id {body.scenario_id!r}")
    if not idempotency_key or not await service.get_by_key(tenant, idempotency_key):
        await service.expire_idle(tenant)
        if await service.active_count(tenant) >= service.MAX_ACTIVE_PER_TENANT:
            raise HTTPException(429, "You have 5 investigations still open. Finish or cancel one "
                                     "(step-mode runs close themselves after 15 idle minutes).")
        client = tenant if tenant != PUBLIC_TENANT else (request.client.host if request.client else "unknown")
        if not guard.allow_create(client):
            raise HTTPException(429, "Demo limit: at most "
                                     f"{service.settings.AUTOPILOT_CREATES_PER_10MIN} new investigations "
                                     "per 10 minutes. Open a recent one meanwhile.")
    state, created = await service.create(tenant, body, idempotency_key)
    if created and body.auto_run and service.settings.AUTOPILOT_EXECUTION != "worker":
        await service.drive(state["run_id"], tenant)       # worker mode: a worker claims it
    return JSONResponse({"run_id": state["run_id"], "status": state["status"], "created": created},
                        status_code=202 if created else 200)


@router.get("/investigations")
async def list_(limit: int = Query(25, ge=1, le=100), tenant: str = Depends(autopilot_tenant)) -> dict:
    return {"investigations": await service.list_(tenant, limit)}


@router.get("/investigations/{investigation_id}")
async def get_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> dict:
    s = service.public(await _load(investigation_id, tenant))
    return {**s, **await service.dispatch_info(investigation_id)}


@router.get("/investigations/{investigation_id}/events")
async def events_(investigation_id: str, after: int = Query(0, ge=0),
                  tenant: str = Depends(autopilot_tenant)) -> dict:
    await _load(investigation_id, tenant)
    return {"events": await service.events(investigation_id, tenant, after)}


@router.get("/investigations/{investigation_id}/evidence")
async def evidence_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> dict:
    s = service.public(await _load(investigation_id, tenant))
    return {k: s[k] for k in ("findings", "hypotheses", "belief_history", "research")}


@router.get("/investigations/{investigation_id}/experiments")
async def experiments_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> dict:
    s = service.public(await _load(investigation_id, tenant))
    return {k: s[k] for k in ("experiments", "rankings", "denials", "budget")}


@router.post("/investigations/{investigation_id}/next")
async def next_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> dict:
    s = await _load(investigation_id, tenant)
    if Status(s["status"]) in TERMINAL:
        raise HTTPException(409, f"investigation is {s['status']}")
    if service.settings.AUTOPILOT_EXECUTION == "worker":
        await service.request_step(investigation_id, tenant)
    else:
        await service.drive(investigation_id, tenant, single_step=True)
    s = service.public(await _load(investigation_id, tenant))
    return {**s, **await service.dispatch_info(investigation_id)}


@router.post("/investigations/{investigation_id}/cancel")
async def cancel_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> dict:
    s = await service.request_cancel(investigation_id, tenant)
    if s is None:
        raise HTTPException(404, "Investigation not found")
    return {"run_id": investigation_id, "status": s["status"], "cancel_requested": True}


@router.get("/investigations/{investigation_id}/receipt")
async def receipt_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> dict:
    return service.receipt(await _load(investigation_id, tenant))


@router.get("/investigations/{investigation_id}/export")
async def export_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> Response:
    body = json.dumps(service.receipt(await _load(investigation_id, tenant)), indent=2, default=str)
    return Response(body, media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="autopilot-receipt-{investigation_id[:8]}.json"'})


@router.post("/investigations/{investigation_id}/review")
async def review_(investigation_id: str, body: ReviewRequest, tenant: str = Depends(autopilot_tenant)) -> dict:
    try:
        s = await service.review(investigation_id, tenant, body.action, body.reviewer, body.note)
    except KeyError:
        raise HTTPException(404, "Investigation not found") from None
    except PermissionError as e:
        raise HTTPException(409, str(e)) from None
    return {"run_id": investigation_id, "status": s["status"], "review": s["review"]}


@router.get("/benchmark")
async def benchmark_() -> dict:
    if not BENCHMARK_FILE.exists():
        return {"available": False,
                "how_to_run": "cd backend && python -m app.autopilot.benchmark --seeds 5"}
    return {"available": True, **json.loads(BENCHMARK_FILE.read_text(encoding="utf-8"))}


@router.get("/ablation")
async def ablation_() -> dict:
    """Live planner (rules vs Nemotron) and Tavily on/off ablation, if it has been run."""
    if not ABLATION_FILE.exists():
        return {"available": False, "how_to_run": "python scripts/run_live_ablation.py --seeds 5"}
    data = json.loads(ABLATION_FILE.read_text(encoding="utf-8"))
    return {"available": True, **{k: v for k, v in data.items() if k not in ("rows", "tavily_rows")}}


@router.get("/benchmark/public-data")
async def public_benchmark_() -> dict:
    path = BENCHMARK_FILE.with_name("adult_benchmark.json")
    return {"available": True, **json.loads(path.read_text(encoding="utf-8"))} if path.exists() else {"available": False}


# ── Bring your own incident ──────────────────────────────────────────────────
async def _read_limited(f: UploadFile, name: str) -> bytes:
    data = await f.read(uploads.MAX_BYTES + 1)
    if len(data) > uploads.MAX_BYTES:
        raise HTTPException(413, f"{name}: larger than {uploads.MAX_BYTES // (1024 * 1024)} MB")
    return data


@router.post("/uploads", status_code=201)
async def upload_create_(reference: UploadFile = File(...), current: UploadFile = File(...),
                         tenant: str = Depends(autopilot_tenant)) -> dict:
    """Step 1: upload the reference (healthy, labelled) and current (recent) windows."""
    ref, cur = await _read_limited(reference, "reference"), await _read_limited(current, "current")
    try:
        return uploads.inspect_and_store(tenant, ref, cur)
    except uploads.UploadError as e:
        raise HTTPException(422, str(e)) from None


@router.get("/uploads")
async def upload_list_(tenant: str = Depends(autopilot_tenant)) -> dict:
    return {"uploads": uploads.list_for(tenant)}


@router.get("/uploads/{upload_id}")
async def upload_get_(upload_id: str, tenant: str = Depends(autopilot_tenant)) -> dict:
    try:
        return uploads.get(tenant, upload_id)
    except (KeyError, uploads.UploadError):
        raise HTTPException(404, "upload not found") from None


@router.post("/uploads/{upload_id}/configure")
async def upload_configure_(upload_id: str, spec: uploads.UploadSpec,
                            tenant: str = Depends(autopilot_tenant)) -> dict:
    """Step 2: say what the target is, how the model is served, and what changed."""
    try:
        return uploads.configure(tenant, upload_id, spec)
    except KeyError:
        raise HTTPException(404, "upload not found") from None
    except uploads.UploadError as e:
        raise HTTPException(422, str(e)) from None


@router.delete("/uploads/{upload_id}", status_code=204)
async def upload_delete_(upload_id: str, tenant: str = Depends(autopilot_tenant)) -> Response:
    try:
        await service.delete_upload(tenant, upload_id)
    except (KeyError, uploads.UploadError):
        raise HTTPException(404, "upload not found") from None
    except PermissionError as e:
        raise HTTPException(409, str(e)) from None
    return Response(status_code=204)


@router.post("/investigations/{investigation_id}/share")
async def share_create_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> dict:
    st = await _load(investigation_id, tenant)
    if Status(st["status"]) not in TERMINAL:
        raise HTTPException(409, "Finish the investigation before sharing it")
    token, expires = sharing.sign(investigation_id)
    return {"token": token, "expires_at": expires, "path": f"/autopilot/share/{token}"}


@router.get("/shared/{token}")
async def share_get_(token: str) -> JSONResponse:
    try:
        run_id = sharing.resolve(token)
    except ValueError:
        raise HTTPException(404, "Invalid or expired share link") from None
    st = await service.shared_get(run_id)
    if st is None:
        raise HTTPException(404, "Investigation no longer available")
    return JSONResponse(st, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/showcase")
async def showcase_() -> dict:
    path = BENCHMARK_FILE.parent / "autopilot_showcase.json"
    if not path.exists():
        raise HTTPException(503, "Showcase unavailable")
    return json.loads(path.read_text(encoding="utf-8"))


class AlertHook(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: str = Field(pattern=r"^[0-9a-f]{12}$")
    source: str = Field(default="monitoring", max_length=80)
    alert_id: str = Field(min_length=1, max_length=100)


@router.post("/hooks/alert", status_code=202)
async def alert_(body: AlertHook, request: Request, tenant: str = Depends(autopilot_tenant),
                 x_alert_token: str | None = Header(default=None)) -> JSONResponse:
    expected = service.settings.AUTOPILOT_ALERT_TOKEN
    if not expected or not x_alert_token or not hmac.compare_digest(expected, x_alert_token):
        raise HTTPException(401, "Invalid alert token")
    # Adapter contract: monitoring systems reference an already configured private upload.
    # No arbitrary URL fetching or outbound Slack messages take place here.
    import hashlib
    key = "alert-" + hashlib.sha256(f"{body.source}:{body.alert_id}".encode()).hexdigest()
    return await create_(CreateInvestigation(scenario_id=f"upload:{body.upload_id}"), request, tenant, key)


@router.get("/investigations/{investigation_id}/summary")
async def summary_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> dict:
    st = await _load(investigation_id, tenant)
    d = st.get("decision") or {}
    text = f"{(st.get('scenario') or {}).get('title', 'Investigation')}: {d.get('summary', st['status'])}"
    return {"text": text, "blocks": [{"type": "section", "text": {"type": "plain_text", "text": text[:2900]}}],
            "next_action": d.get("next_action"), "run_id": investigation_id}


SAMPLES = {
    "adult-adapter": {"scenario": "A01", "title": "Public data: UCI Adult adapter incident",
                      "symptom": "Income classifier quality fell after an adapter release.",
                      "notes": "UCI Adult CC BY 4.0. Random windows with an injected age-unit fault, not a real production outage.",
                      "serving_threshold": 0.4},
    "units-bug": {"scenario": "S02", "title": "Loan model: false alarms after a data platform release",
                  "symptom": "Approvals team reports many false alarms since Tuesday.",
                  "notes": "Data platform shipped ingestion v2.3 on Tuesday: 'standardised monetary fields'.",
                  "serving_threshold": 0.3},
    "threshold-change": {"scenario": "S03", "title": "Loan model: recall dropped after a config deploy",
                         "symptom": "Collections says we miss defaulters we used to catch.",
                         "notes": "Last Thursday's deploy touched the scoring service config.",
                         "serving_threshold": 0.62, "trained_threshold": 0.3},
    "contract-break": {"scenario": "S04", "title": "Loan model: odd scores after a vendor switch",
                       "symptom": "Scores look strange since the bureau vendor switch.",
                       "notes": "New vendor may omit employment history for thin-file applicants.",
                       "serving_threshold": 0.3},
}


@router.get("/samples")
async def samples_() -> dict:
    """Sample CSV pairs (synthetic loan data) with suggested settings, to try the upload flow."""
    return {"samples": [{"id": k, **{x: v[x] for x in ("title", "symptom", "notes")},
                         "suggested": {"target": "defaulted", "positive_label": "1", "metric": "f1",
                                       "model_family": "logistic_regression",
                                       "serving_threshold": v["serving_threshold"],
                                       "trained_threshold": v.get("trained_threshold"),
                                       "segment_column": "region", "exclude_columns": ["inquiries_6m"]}
                         if k != "adult-adapter" else {"target": "above_50k", "positive_label": "1", "metric": "f1",
                            "model_family": "logistic_regression", "serving_threshold": 0.4,
                            "exclude_columns": ["fnlwgt", "education", "race", "sex", "native_country"]}}
                        for k, v in SAMPLES.items()]}


@router.get("/samples/{sample_id}/{window}.csv")
async def sample_csv_(sample_id: str, window: str) -> Response:
    if sample_id not in SAMPLES or window not in ("reference", "current"):
        raise HTTPException(404, "unknown sample")
    from app.autopilot import scenarios
    pack = scenarios.build(SAMPLES[sample_id]["scenario"], 7)
    df = pack.reference if window == "reference" else pack.current
    return Response(df.to_csv(index=False), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{sample_id}-{window}.csv"'})
