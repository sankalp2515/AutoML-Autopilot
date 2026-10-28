"""Restricted runner.

Executes ONE approved template by id with validated parameters. In "process" mode
the template runs in a fresh spawned child process that is killed when the wall
clock limit expires, so a hung or runaway experiment cannot stall the control
plane. "inline" mode exists for unit tests and the benchmark harness and is
labelled in every receipt it produces.

Limits honestly stated: the child has no network-specific sandbox on a developer
laptop; isolation comes from the fact that it only ever runs vetted template code.
In Docker the existing sandbox container's no-network policy can host it.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import time
import traceback
from typing import Any

from app.autopilot import templates, workspace


def _child(template_id: str, ws_root: str, params: dict, exp_id: str, q) -> None:
    workspace.lock_final_test()                 # this process never needs the holdout
    try:
        q.put(("ok", templates.execute(template_id, ws_root, params, exp_id)))
    except Exception as e:  # noqa: BLE001 — every failure is reported, never swallowed
        q.put(("error", f"{type(e).__name__}: {str(e)[:300]}", traceback.format_exc(limit=3)[-800:]))


def run(template_id: str, ws_root: str, params: dict, exp_id: str, timeout_s: float,
        mode: str = "process") -> dict[str, Any]:
    t0 = time.perf_counter()
    if mode == "inline":
        prev = workspace.lock_final_test()
        try:
            res = templates.execute(template_id, ws_root, params, exp_id)
            return {"status": "DONE", "result": res, "seconds": round(time.perf_counter() - t0, 3), "mode": mode}
        except Exception as e:  # noqa: BLE001
            return {"status": "FAILED", "error": f"{type(e).__name__}: {str(e)[:300]}",
                    "seconds": round(time.perf_counter() - t0, 3), "mode": mode}
        finally:
            workspace.lock_final_test(prev)
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    proc = ctx.Process(target=_child, args=(template_id, ws_root, params, exp_id, q), daemon=True)
    proc.start()
    deadline = t0 + timeout_s
    msg = None
    while time.perf_counter() < deadline:
        try:
            msg = q.get(timeout=0.25)
            break
        except Exception:  # queue.Empty
            if not proc.is_alive() and q.empty():
                break
    secs = round(time.perf_counter() - t0, 3)
    if msg is None:
        if proc.is_alive():
            proc.terminate()
            proc.join(5)
            return {"status": "TIMEOUT", "error": f"E_TIMEOUT: exceeded {timeout_s}s; process killed",
                    "seconds": secs, "mode": mode}
        return {"status": "FAILED", "error": f"E_WORKER_CRASH: exit code {proc.exitcode}", "seconds": secs,
                "mode": mode}
    proc.join(5)
    if msg[0] == "ok":
        return {"status": "DONE", "result": msg[1], "seconds": secs, "mode": mode}
    return {"status": "FAILED", "error": msg[1], "seconds": secs, "mode": mode}


# ── verifier process ─────────────────────────────────────────────────────────
def _verify_job(ws_root: str, kwargs: dict, may_open: bool) -> dict[str, Any]:
    from app.autopilot.verifier import verify
    ws = workspace.Workspace(ws_root)
    opened: list[str] = []

    def open_final_test(label: str):
        if not may_open:
            raise PermissionError("final test already used; holdout is single-use")
        opened.append(label)
        return ws.rows("final_test")

    workspace.lock_final_test(False)
    rec = verify(ws, open_final_test=open_final_test, **kwargs)
    return {"record": rec, "opened_for": opened[0] if opened else None, "pid": os.getpid()}


def _verify_child(ws_root: str, kwargs: dict, may_open: bool, q) -> None:
    try:
        q.put(("ok", _verify_job(ws_root, kwargs, may_open)))
    except Exception as e:  # noqa: BLE001
        q.put(("error", f"{type(e).__name__}: {str(e)[:300]}"))


def run_verifier(ws_root: str, kwargs: dict, may_open: bool, timeout_s: float,
                 mode: str = "process") -> dict[str, Any]:
    """Run the verifier in its own process: the only process allowed to read final-test
    rows. Returns {"status", "record", "opened_for", "pid"}; any failure is FAILED, never
    a verdict."""
    t0 = time.perf_counter()
    if mode == "inline":
        prev = workspace.lock_final_test(False)
        try:
            return {"status": "DONE", **_verify_job(ws_root, kwargs, may_open),
                    "seconds": round(time.perf_counter() - t0, 3), "mode": mode}
        except Exception as e:  # noqa: BLE001
            return {"status": "FAILED", "error": f"{type(e).__name__}: {str(e)[:300]}", "mode": mode}
        finally:
            workspace.lock_final_test(prev)
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    proc = ctx.Process(target=_verify_child, args=(ws_root, kwargs, may_open, q), daemon=True)
    proc.start()
    msg = None
    while time.perf_counter() < t0 + timeout_s:
        try:
            msg = q.get(timeout=0.25)
            break
        except Exception:  # queue.Empty
            if not proc.is_alive() and q.empty():
                break
    secs = round(time.perf_counter() - t0, 3)
    if msg is None:
        if proc.is_alive():
            proc.terminate()
            proc.join(5)
        return {"status": "FAILED", "error": "E_VERIFIER_UNAVAILABLE: verifier process did not answer",
                "seconds": secs, "mode": mode}
    proc.join(5)
    if msg[0] == "ok":
        return {"status": "DONE", **msg[1], "seconds": secs, "mode": mode}
    return {"status": "FAILED", "error": msg[1], "seconds": secs, "mode": mode}


def verify_remote(url: str, token: str, run_id: str, kwargs: dict, may_open: bool,
                  timeout_s: float) -> dict[str, Any]:
    """Call the verifier service (separate container). Any failure is FAILED, never a verdict."""
    import httpx
    t0 = time.perf_counter()
    try:
        r = httpx.post(f"{url.rstrip('/')}/verify", headers={"X-Verifier-Token": token}, timeout=timeout_s,
                       json={"run_id": run_id, **kwargs, "may_open": may_open})
        secs = round(time.perf_counter() - t0, 3)
        if r.status_code != 200:
            return {"status": "FAILED", "error": f"E_VERIFIER_UNAVAILABLE: HTTP {r.status_code}",
                    "seconds": secs, "mode": "service"}
        return {"status": "DONE", **r.json(), "seconds": secs, "mode": "service"}
    except httpx.HTTPError as e:
        return {"status": "FAILED", "error": f"E_VERIFIER_UNAVAILABLE: {type(e).__name__}",
                "seconds": round(time.perf_counter() - t0, 3), "mode": "service"}
