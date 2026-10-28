"""Discovery Agent v1 (MLE-STAR-style model discovery, permission-gated).

The scout already *recommends* not-installed models (registry `installed: False`
entries surfaced as discovery_notes). This module turns those recommendations
into a governed install pipeline:

  scout note → proposal (deterministic parse) → vetting (tier allowlist +
  static safety catalog) → HUMAN approval (ask-mode) → host-side image rebuild
  (scripts/apply_discovery.ps1) → installed marker → registry flips trainable.

Security invariants (v1):
- The RUNTIME sandbox stays egress-free: installs happen at image BUILD time
  via a pinned requirements-extra.txt; nothing is ever pip-installed at runtime.
- v1 vets against a STATIC tier-A catalog (no live web calls) — pinned versions
  of widely-used, pure-pip ML libraries. Live PyPI/OSV vetting is v2.
- Anything with system-level deps (apt) is rejected on this host (port 80
  blocked → apt unavailable at build time).
- A newly installed model still has to WIN measured CV like every other
  candidate — discovery proposes, sklearn disposes.

State lives under DATA_DIR/discovery/ (durable volume, shared API/worker):
  proposals.json  — {pkg: {status: pending|approved|rejected, ...}}
  installed.json  — [pkg, ...] written by the apply script AFTER verifying the
                    import inside the rebuilt sandbox image.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.core.logging import get_logger

_log = get_logger("discovery")

# Tier A — pre-vetted, pinned, pure-pip (no system deps beyond the base image).
TIER_A: dict[str, dict[str, Any]] = {
    "catboost": {
        "version": "1.2.7",
        "license": "Apache-2.0",
        "import_name": "catboost",
        "system_deps": [],
        "registry_family": "CatBoost",
        "claim": "Native high-cardinality categorical handling; often beats XGB on categorical-heavy tabular data.",
    },
    "lightgbm": {
        "version": "4.5.0",
        "license": "MIT",
        "import_name": "lightgbm",
        "system_deps": ["libgomp1"],   # apt — unavailable on this host (port 80 blocked)
        "registry_family": "LightGBM",
        "claim": "Very fast leaf-wise boosting for large datasets.",
    },
}

_PKG_RE = re.compile(r"pip:\s*([a-zA-Z0-9_\-]+)")


def _dir() -> str:
    return os.path.join(settings.DATA_DIR, "discovery")


def _proposals_path() -> str:
    return os.path.join(_dir(), "proposals.json")


def _installed_path() -> str:
    return os.path.join(_dir(), "installed.json")


def _load(path: str, default):
    try:
        # utf-8-sig: the installed marker is written from PowerShell on the host,
        # which prefixes a BOM — plain utf-8 json.load rejects it.
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _save(path: str, doc) -> None:
    os.makedirs(_dir(), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, default=str)
    os.replace(tmp, path)


def installed_packages() -> set[str]:
    """Packages VERIFIED importable in the current sandbox image (written by the
    apply script after rebuild — never assumed)."""
    doc = _load(_installed_path(), [])
    return {p for p in doc if isinstance(p, str)} if isinstance(doc, list) else set()


def vet(pkg: str) -> dict[str, Any]:
    """Deterministic v1 vetting: tier allowlist + host constraints."""
    spec = TIER_A.get(pkg)
    if not spec:
        return {"ok": False, "tier": "C",
                "reason": f"'{pkg}' is not on the tier-A allowlist (v1 accepts only pre-vetted packages)."}
    if spec["system_deps"]:
        return {"ok": False, "tier": "A",
                "reason": f"'{pkg}' needs system packages {spec['system_deps']} — apt is unavailable "
                          "on this host (outbound port 80 blocked), so the image build would fail."}
    return {"ok": True, "tier": "A", "pinned": f"{pkg}=={spec['version']}",
            "license": spec["license"], "claim": spec["claim"]}


def record_proposal(discovery_notes: str, run_id: str, task_type: str = "") -> dict | None:
    """Parse a scout note ('... (pip: catboost) ...') into a vetted proposal.
    Ask-mode: nothing installs without an explicit approve call."""
    if not discovery_notes:
        return None
    m = _PKG_RE.search(discovery_notes)
    if not m:
        return None
    pkg = m.group(1).lower()
    if pkg in installed_packages():
        return None
    try:
        proposals = _load(_proposals_path(), {})
        if not isinstance(proposals, dict):
            proposals = {}
        existing = proposals.get(pkg)
        if existing and existing.get("status") in ("approved", "rejected"):
            return existing  # user already decided — don't nag
        verdict = vet(pkg)
        proposal = {
            "package": pkg,
            "pinned": verdict.get("pinned", pkg),
            "status": "pending" if verdict["ok"] else "blocked",
            "vet": verdict,
            "claim": TIER_A.get(pkg, {}).get("claim", ""),
            "first_proposed_by_run": existing.get("first_proposed_by_run") if existing else run_id,
            "task_type": task_type,
            "times_proposed": int(existing.get("times_proposed", 0) if existing else 0) + 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        proposals[pkg] = proposal
        _save(_proposals_path(), proposals)
        _log.info("discovery_proposal", package=pkg, status=proposal["status"], run_id=run_id)
        return proposal
    except Exception as e:  # discovery must never break a run
        _log.warning("discovery_record_failed", error=str(e)[:200])
        return None


def list_proposals() -> list[dict]:
    doc = _load(_proposals_path(), {})
    return list(doc.values()) if isinstance(doc, dict) else []


def decide(pkg: str, approve: bool) -> dict:
    """Human decision (ask-mode gate). Approval stages the pinned requirement for
    the host-side rebuild — it does NOT install anything at runtime."""
    proposals = _load(_proposals_path(), {})
    if not isinstance(proposals, dict) or pkg not in proposals:
        raise KeyError(f"no proposal for '{pkg}'")
    p = proposals[pkg]
    verdict = vet(pkg)
    if approve and not verdict["ok"]:
        raise ValueError(f"cannot approve: {verdict['reason']}")
    p["status"] = "approved" if approve else "rejected"
    p["decided_at"] = datetime.now(timezone.utc).isoformat()
    proposals[pkg] = p
    _save(_proposals_path(), proposals)

    if approve:
        # Stage the pinned requirement where the host apply-script picks it up.
        staged = os.path.join(_dir(), "approved-requirements.txt")
        lines = set()
        if os.path.exists(staged):
            with open(staged, "r", encoding="utf-8") as fh:
                lines = {ln.strip() for ln in fh if ln.strip()}
        lines.add(verdict["pinned"])
        with open(staged, "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(lines)) + "\n")
        p["next_step"] = ("Staged. Run scripts/apply_discovery.ps1 on the host to rebuild the "
                          "sandbox image with the approved package(s) and verify the install.")
    _log.info("discovery_decision", package=pkg, status=p["status"])
    return p
