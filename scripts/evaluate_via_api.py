"""Product-path evaluation: run bundled samples through a running v5 stack's public API.

Each slot stages a sample, creates a task in `full` (arm D, our agent) or `generic`
(arm A, a "make the tests pass" agent) mode and waits for the independent verifier's
verdict. Nothing is graded here: the decision, receipts and report come from the stack.
Results are append-only; an existing slot directory is skipped, never overwritten.

    python scripts/evaluate_via_api.py --base http://127.0.0.1:3137 --out docs/benchmarks/repair/api-eval-2026-10-30
"""
import argparse
import json
import time
import uuid
from pathlib import Path

import httpx

FINAL = {"REVIEW_READY", "PR_DRAFTED", "REJECTED", "INCONCLUSIVE", "BLOCKED", "FAILED", "CANCELLED"}
ARMS = {"D": "full", "A": "generic"}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--slots", nargs="+", required=True, help="fixture:arm, e.g. refund-risk:A churn-service:D")
    p.add_argument("--timeout-min", type=int, default=35)
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    api = a.base.rstrip("/") + "/api/v1/v5"
    with httpx.Client(timeout=60) as h:
        tasks = {s["name"]: s["task"] for s in h.get(f"{api}/samples").json()["samples"]}
        for slot in a.slots:
            fixture, arm = slot.split(":")
            dest = out / f"{fixture}-{arm}"
            if (dest / "result.json").exists():
                continue
            dest.mkdir(exist_ok=True)
            hdr = {"X-Autopilot-Session": f"eval-{uuid.uuid4().hex[:20]}"}
            t0 = time.time()
            repo = h.post(f"{api}/repos", json={"sample": fixture}, headers=hdr)
            repo.raise_for_status()
            r = h.post(f"{api}/tasks", headers=hdr, json={"repo_id": repo.json()["id"], "target": tasks[fixture],
                                                           "mode": ARMS[arm]})
            if r.status_code != 201:
                row = {"fixture": fixture, "arm": arm, "status": "NOT_ADMITTED", "detail": r.text[:300]}
            else:
                tid = r.json()["id"]
                print(json.dumps({"slot": slot, "task": tid, "stage": "started"}), flush=True)
                task = r.json()
                while task["status"] not in FINAL and time.time() - t0 < a.timeout_min * 60:
                    time.sleep(20)
                    try:
                        task = h.get(f"{api}/tasks/{tid}", headers=hdr).json()
                    except httpx.HTTPError:
                        continue
                rep = h.get(f"{api}/tasks/{tid}/report", headers=hdr)
                if rep.status_code == 200:
                    (dest / "report.json").write_text(json.dumps(rep.json(), indent=2), encoding="utf-8")
                ev = h.get(f"{api}/tasks/{tid}/events?limit=500", headers=hdr)
                if ev.status_code == 200:
                    (dest / "events.json").write_text(json.dumps(ev.json(), indent=2), encoding="utf-8")
                ml = ((rep.json() if rep.status_code == 200 else {}).get("final_ml_receipt") or {})
                rejected_ml = sorted({c["name"] for r_ in ((rep.json() if rep.status_code == 200 else {})
                                                           .get("candidates") or [])
                                      for c in ((r_.get("ml_receipt") or r_.get("screen_receipt") or {})
                                                .get("checks") or []) if c.get("status") == "FAIL"})
                row = {"fixture": fixture, "arm": arm, "mode": ARMS[arm], "task_id": tid, "status": task["status"],
                       "decision": task.get("decision"), "reasons": task.get("reasons"),
                       "finalist": task.get("finalist"), "cost_usd": task.get("cost_usd"),
                       "seconds": task.get("seconds"), "wall_seconds": round(time.time() - t0, 1),
                       "holdout": (ml.get("metrics") or {}).get("holdout"), "ml_checks_failed": rejected_ml,
                       "timed_out_waiting": task["status"] not in FINAL}
            (dest / "result.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
            with (out / "results.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            print(json.dumps(row), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
