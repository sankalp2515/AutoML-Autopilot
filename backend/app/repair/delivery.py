"""Delivery (BE-10): `.patch` text and a GitHub draft pull request. Never merges.

The user's GitHub token is used for one request sequence and never stored or logged.
The PR is built with the Git Data API on top of the upstream base commit, so no local
push credentials or Git remotes are involved.
"""
from __future__ import annotations

import base64
import re
from typing import Any

import httpx

from app.repair.oracle.common import apply_diff, parse_diff

API = "https://api.github.com"


class DeliveryError(RuntimeError):
    pass


def pr_body(task: dict, report: dict) -> str:
    v = report.get("verdict") or {}
    fin = next((r for r in report.get("candidates", []) if r["candidate"]["candidate_id"] == report.get("finalist")), {})
    lines = [f"## AutoML Autopilot repair — {report.get('decision')}", "", task.get("target", ""), "",
             f"**Root cause and fix (agent summary, verified below):** {fin.get('candidate', {}).get('summary', '')}", "",
             "### Independent checks"]
    for name in ("code_receipt",):
        for ch in (fin.get(name) or {}).get("checks", []):
            lines.append(f"- code · {ch['name']}: **{ch['status']}** — {ch['detail']}")
    for ch in (report.get("final_ml_receipt") or {}).get("checks", []):
        lines.append(f"- ML · {ch['name']}: **{ch['status']}** — {ch['detail']}")
    m = (report.get("final_ml_receipt") or {}).get("metrics", {}).get("holdout")
    if m:
        lines += ["", f"Holdout {m.get('metric')}: {m.get('baseline')} → {m.get('candidate')} ({m.get('rows')} rows)"]
    lines += ["", f"Contract digest `{task.get('contract_digest')}` · receipt verdict `{v.get('decision')}`",
              f"Model usage: {report.get('usage', {}).get('total_est_cost_usd')} USD (estimate)", "",
              "_Draft opened by AutoML Autopilot. Nothing was merged; a human reviews and decides._"]
    return "\n".join(lines)


def create_draft_pr(*, token: str, repo: str, base_sha: str, base_branch: str, snapshot_dir: str, diff: str,
                    title: str, body: str, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise DeliveryError("invalid repository or base commit")
    files = apply_diff(snapshot_dir, diff)
    if any(ch.binary for ch in parse_diff(diff)):
        raise DeliveryError("binary changes are not delivered")
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    with httpx.Client(base_url=API, headers=h, timeout=30, transport=transport) as gh:
        def call(method: str, path: str, **kw):
            r = gh.request(method, path, **kw)
            if r.status_code >= 300:
                raise DeliveryError(f"GitHub {method} {path.split('?')[0]}: HTTP {r.status_code}")
            return r.json()
        base_tree = call("GET", f"/repos/{repo}/git/commits/{base_sha}")["tree"]["sha"]
        tree = []
        for path, content in files.items():
            if content is None:
                tree.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            else:
                blob = call("POST", f"/repos/{repo}/git/blobs",
                            json={"content": base64.b64encode(content).decode(), "encoding": "base64"})
                tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        new_tree = call("POST", f"/repos/{repo}/git/trees", json={"base_tree": base_tree, "tree": tree})["sha"]
        commit = call("POST", f"/repos/{repo}/git/commits",
                      json={"message": title, "tree": new_tree, "parents": [base_sha]})["sha"]
        branch = f"autopilot/repair-{commit[:10]}"
        call("POST", f"/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": commit})
        pr = call("POST", f"/repos/{repo}/pulls", json={"title": title, "head": branch, "base": base_branch,
                                                         "body": body, "draft": True})
    return {"url": pr.get("html_url"), "number": pr.get("number"), "branch": branch, "commit": commit}
