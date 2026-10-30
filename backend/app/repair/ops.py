"""Operations for v5: live-run admission and spend caps (BE-12), provider/runtime status
(BE-18), readiness (BE-22), retention cleanup (BE-20) and replays (BE-11)."""
from __future__ import annotations

import hmac
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.repair.oracle.grading import packs_root, snapshots_root


class Denied(PermissionError):
    pass


def validate_secrets(*names):
    values=[getattr(settings,name) for name in names]
    if any(len(value)<32 or value.startswith("change-me") for value in values) or len(set(values))!=len(values):
        raise ValueError("configure distinct random secrets of at least 32 characters: "+", ".join(names))


def reservation_policy(requested_budget=None):
    from app.repair.budgets import estimate
    e=estimate(requested_budget)
    return {"tenant_tasks":settings.REPAIR_DAILY_TASKS_PER_TENANT,
            "cap_usd":settings.REPAIR_DAILY_USD_CAP,"cap_tokens":settings.REPAIR_DAILY_TOKEN_CAP,
            "reserve_usd":max(settings.REPAIR_TASK_USD_CEILING,e["estimated_inference_usd"]),
            "total_cap_usd":settings.REPAIR_TOTAL_USD_CAP,
            "cooldown_s":settings.REPAIR_CREATE_COOLDOWN_S,
            "max_active":settings.REPAIR_MAX_ACTIVE_TASKS,
            "reserve_tokens":sum(e["limits"]["model_tokens"].values())}


# ── BE-12 admission ───────────────────────────────────────────────────────────
async def admit_live_run(svc, tenant: str, invite_code: str | None, *, trusted: bool = False,
                         requested_budget=None, skip_capacity=False) -> dict[str, Any]:
    """Live runs spend real money once Nebius is on. Refuse unless enabled, invited and under caps.
    Spend = measured cost of today's finished runs + a worst-case reservation per unfinished run.
    `trusted`: outside judge-only mode, a signed-in member or linked repository needs no invite code;
    the daily task and dollar caps still apply."""
    if not settings.REPAIR_LIVE_ENABLED:
        raise Denied("Live runs are switched off on this server. Watch a recorded run instead.")
    codes = [x.strip() for x in settings.REPAIR_INVITE_CODES.split(",") if x.strip()]
    invited=any(hmac.compare_digest(invite_code or "", x) for x in codes)
    if settings.REPAIR_JUDGE_ONLY and (not codes or not invited):
        raise Denied("Live evaluation is reserved for invited judges. Enter the judge access code supplied in the testing instructions. Recorded evidence remains available without a code.")
    if codes and not trusted and not invited:
        raise Denied("A valid invite code is required for live runs.")
    policy=reservation_policy(requested_budget)
    if skip_capacity:
        return policy  # The authoritative check and reservation occur with task creation.
    day = time.time() - 86400
    rows = await svc.since(day)
    mine = sum(1 for t, _, _ in rows if t == tenant)
    if mine >= settings.REPAIR_DAILY_TASKS_PER_TENANT:
        raise Denied(f"Daily limit of {settings.REPAIR_DAILY_TASKS_PER_TENANT} live runs reached.")
    spent = sum(((r or {}).get("usage") or {}).get("total_est_cost_usd") or 0 for _, _, r in rows)
    pending = sum(1 for _, s, r in rows if not r and s not in ("CANCELLED", "FAILED"))
    committed = spent + pending * settings.REPAIR_TASK_USD_CEILING
    if committed + settings.REPAIR_TASK_USD_CEILING > settings.REPAIR_DAILY_USD_CAP:
        raise Denied("Today's live-run budget is used up. Watch a recorded run instead.")
    return {**policy,"spent_usd": round(spent, 4), "reserved_usd": round(committed, 4)}


# ── BE-18 status ──────────────────────────────────────────────────────────────
def provider_config() -> dict[str, Any]:
    if settings.NEBIUS_API_KEY:
        prov, models = "nebius_token_factory", {"ultra": settings.NEBIUS_PLANNER_MODEL,
                                                "super": settings.NEBIUS_CODER_MODEL, "nano": settings.NEBIUS_FAST_MODEL}
    elif settings.NVIDIA_API_KEY:
        prov, models = "nvidia_api", {"ultra": settings.NVIDIA_PLANNER_MODEL, "super": settings.NVIDIA_CODER_MODEL,
                                      "nano": settings.NVIDIA_FAST_MODEL}
    else:
        prov, models = None, {}
    return {"provider": prov, "models": models}


async def status(svc) -> dict[str, Any]:
    # The api has no provider keys; the worker reports its configuration with each heartbeat.
    w = await svc.latest_worker() or {}
    cfg = {"provider": w.get("provider"), "models": w.get("models", {})} if w else provider_config()
    recent = await svc.since(time.time() - 7 * 86400)
    ok_calls = [call for _, _, rep in recent for call in (rep or {}).get("calls", []) if call.get("ok")]
    failed = [call for _, _, rep in recent for call in (rep or {}).get("calls", []) if not call.get("ok")]
    return {**cfg, "configured": bool(cfg["provider"]), "successful_calls_7d": len(ok_calls),
            "failed_calls_7d": len(failed), "last_model_used": ok_calls[-1]["model"] if ok_calls else None,
            "last_successful_call_at":max((call["at"] for call in ok_calls if call.get("at")),default=None),
            "tavily": bool(w.get("tavily", settings.TAVILY_API_KEY)), "live_runs": settings.REPAIR_LIVE_ENABLED,
            "worker_seen_s_ago": round(time.time() - w["beat"]) if w else None,
            "invite_required": settings.REPAIR_JUDGE_ONLY or bool(settings.REPAIR_INVITE_CODES.strip()),
            "access_policy":{"judge_only":settings.REPAIR_JUDGE_ONLY,
                "daily_runs_per_workspace":settings.REPAIR_DAILY_TASKS_PER_TENANT,
                "cooldown_seconds":settings.REPAIR_CREATE_COOLDOWN_S,
                "max_active_tasks":settings.REPAIR_MAX_ACTIVE_TASKS,
                "total_estimated_usd_cap":settings.REPAIR_TOTAL_USD_CAP or None,
                "reason":"Live inference is funded by a finite hackathon credit balance. Limits preserve access throughout judging; viewing recordings, receipts and diffs does not spend inference credits."},
            "runner": "sandbox-service" if settings.REPAIR_SANDBOX_URL else settings.REPAIR_RUNNER,
            "isolated": bool(settings.REPAIR_SANDBOX_URL) or settings.REPAIR_RUNNER != "process",
            "verifier": "service" if settings.REPAIR_VERIFIER_URL else "in-process",
            "daily_cap_usd": settings.REPAIR_DAILY_USD_CAP}


# ── BE-22 metrics ─────────────────────────────────────────────────────────────
async def metrics(svc, *, window_s: int = 86400, now: float | None = None) -> dict[str, Any]:
    """Queue age, run time, tokens and cost over the window. No tenant data, no task ids."""
    now = now or time.time()
    rows = await svc.recent(now - window_s)
    queued = [created for status, created, _ in rows if status == "CREATED"]
    done = [r for _, _, r in rows if r]
    secs = sorted(float(r.get("seconds") or 0) for r in done)

    def pct(q: float) -> float | None:
        return round(secs[min(len(secs) - 1, int(q * len(secs)))], 1) if secs else None
    decisions: dict[str, int] = {}
    for r in done:
        decisions[str(r.get("decision"))] = decisions.get(str(r.get("decision")), 0) + 1
    tokens = sum(sum((t.get("prompt_tokens") or 0) + (t.get("completion_tokens") or 0)
                     for t in (r.get("usage") or {}).values() if isinstance(t, dict)) for r in done)
    costs=[(r.get("usage") or {}).get("total_est_cost_usd") for r in done]
    unknown=sum(value is None for value in costs)
    known=round(sum(value for value in costs if value is not None),4)
    return {"window_hours": round(window_s / 3600, 1), "tasks": len(rows), "queue_depth": len(queued),
            "oldest_queued_s": round(now - min(queued)) if queued else 0, "finished": len(done),
            "decisions": decisions, "run_seconds_p50": pct(0.5), "run_seconds_p95": pct(0.95),
            "model_tokens": tokens,
            "cost_usd":None if unknown else known,"known_cost_usd":known,"unknown_cost_runs":unknown}


def prometheus(m: dict[str, Any]) -> str:
    lines = []
    for k in ("tasks", "queue_depth", "oldest_queued_s", "finished", "run_seconds_p50", "run_seconds_p95",
              "model_tokens", "cost_usd", "known_cost_usd", "unknown_cost_runs"):
        if m.get(k) is not None:
            lines += [f"# TYPE autopilot_repair_{k} gauge", f"autopilot_repair_{k} {m[k]}"]
    for d, n in m["decisions"].items():
        lines.append(f'autopilot_repair_decisions{{decision="{d}"}} {n}')
    return "\n".join(lines) + "\n"


# ── BE-22 readiness ───────────────────────────────────────────────────────────
async def readiness(svc) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        beat = await svc.latest_beat()
        out["database"] = "ok"
        out["worker"] = "ok" if beat and time.time() - beat < 60 else "stale"
    except Exception as e:  # noqa: BLE001
        out["database"], out["worker"] = f"error: {type(e).__name__}", "unknown"
    for name, url in (("verifier", settings.REPAIR_VERIFIER_URL), ("sandbox", settings.REPAIR_SANDBOX_URL)):
        if not url:
            out[name] = "in-process"
            continue
        try:
            async with httpx.AsyncClient(timeout=3) as cl:
                response=await cl.get(url.rstrip("/")+("/v5/oracle/ready" if name=="verifier" else "/health"))
                out[name]="ok" if response.status_code==200 and (name!="verifier" or response.json().get("ready")) else "down"
        except httpx.HTTPError:
            out[name] = "down"
    out["ready"] = all(v in ("ok", "in-process") for k, v in out.items())
    return out


# ── BE-20 cleanup ─────────────────────────────────────────────────────────────
async def cleanup(svc, *, now: float | None = None) -> dict[str, int]:
    """Delete staged repos, snapshots, packs, verifier caches and stray environments older than
    the retention window, unless an unfinished task still references them."""
    now = now or time.time()
    cutoff = now - settings.REPAIR_RETENTION_HOURS * 3600
    refs = set()
    for sp in await svc.active_specs():
        refs |= {str(sp.get("snapshot_dir", "")), str(sp.get("staging_id", "")), str(sp.get("sealed_ref", ""))}
    removed = {}
    base = Path(settings.DATA_DIR)
    targets = {"staging": base / "repair" / "staging", "snapshots": snapshots_root(), "packs": packs_root(),
               "verifier_cache": Path(settings.REPAIR_VERIFIER_STATE_DIR or base / "verifier") / "cache"}
    # Sandbox environment/image retention belongs to the sandbox service, never a
    # worker guessing host paths. Verifier admissions/openings must never expire.
    for name, root in targets.items():
        n = 0
        if root.is_dir():
            for p in root.iterdir():
                if p.is_symlink():continue
                if p.stat().st_mtime > cutoff:
                    continue
                if str(p) in refs or p.name in refs or f"pack:{p.name}" in refs:
                    continue
                if not p.resolve().is_relative_to(root.resolve()):continue
                (shutil.rmtree if p.is_dir() else Path.unlink)(p)
                n += 1
        removed[name] = n
    return removed


async def retention_loop():
    import asyncio
    from app.repair.service import default_service
    while True:
        try:await cleanup(default_service)
        except Exception:pass # Retry next interval; never erase admissions/openings on an error.
        await asyncio.sleep(600)


# ── BE-11 replays ─────────────────────────────────────────────────────────────
def _replay_dirs() -> list[Path]:
    bundled = Path(settings.REPAIR_REPLAYS_DIR or Path(__file__).resolve().parents[3] / "docs" / "benchmarks" / "repair")
    published = Path(settings.DATA_DIR, "repair", "replays")
    return [d for d in (bundled, published) if d.is_dir()]


def list_replays() -> list[dict[str, Any]]:
    out = []
    for d in _replay_dirs():
        for p in sorted(d.glob("*.json")):
            try:
                r = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if "events" not in r or "candidates" not in r:
                continue
            out.append({"id": p.stem, "task_id": r.get("task_id"), "decision": r.get("decision"),
                        "mode": r.get("mode"), "candidates": len(r.get("candidates", [])),
                        "seconds": r.get("seconds"), "cost_usd": (r.get("usage") or {}).get("total_est_cost_usd"),
                        "provider": r.get("provider"), "recorded": True})
    return out


def get_replay(rid: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", rid):
        raise FileNotFoundError(rid)
    for d in _replay_dirs():
        p = d / f"{rid}.json"
        if p.is_file():
            try:r=json.loads(p.read_text(encoding="utf-8"))
            except ValueError:continue
            if not isinstance(r,dict) or not isinstance(r.get("events"),list) or not isinstance(r.get("candidates"),list) or not r.get("task_id"):continue
            return {**r,"replay_id":rid,"recorded":True}
    raise FileNotFoundError(rid)


def publish_replay(task_id: str, report: dict, task: dict) -> str:
    d = Path(settings.DATA_DIR, "repair", "replays")
    d.mkdir(parents=True, exist_ok=True)
    rid = f"run-{task_id[:8]}"
    (d / f"{rid}.json").write_text(json.dumps({**report, "target": task.get("target"),
                                               "sample": task.get("sample")}), encoding="utf-8")
    return rid
