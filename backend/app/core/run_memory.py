"""Phase 3 — cross-run memory (the compounding flywheel).

Persist what the Skeptic (trust gate) and the Hypothesis Loop LEARNED about a
dataset, keyed by a stable dataset signature, and feed it back into later runs
so the Strategist and Skeptic get sharper every time the same (or an evolved)
dataset comes back.

Storage: one small JSON file per signature under DATA_DIR/memory/ — durable
(the /data volume outlives containers), shared by API + worker, and needs no
DB migration. Files are tiny (capped lists), so sync I/O is fine.

Doctrine: memory is EVIDENCE, not authority — readers surface it as prior
findings in prompts/reports; nothing is auto-excluded solely because memory
says so (the probes still have to confirm it on the current run).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.core.logging import get_logger

_log = get_logger("run_memory")

_MAX_RUNS = 20          # per-signature run history
_MAX_FINDINGS = 30      # confirmed / refuted hypotheses kept


def _memory_dir() -> str:
    return os.path.join(settings.DATA_DIR, "memory")


def dataset_signature(dataset_path: str) -> str:
    """Stable signature from the CSV header (sorted, case-normalized column names).

    Sampling, re-runs and train/holdout splits keep the same columns, so all
    runs of one dataset family share a signature. Content is deliberately NOT
    hashed — an updated export of the same dataset should still match.
    """
    try:
        with open(dataset_path, "r", encoding="utf-8-sig", errors="ignore") as fh:
            header = fh.readline().strip()
        cols = sorted(c.strip().strip('"').lower() for c in header.split(",") if c.strip())
        if not cols:
            return ""
        return hashlib.sha256("|".join(cols).encode()).hexdigest()[:16]
    except OSError:
        return ""


def _path_for(sig: str) -> str:
    return os.path.join(_memory_dir(), f"{sig}.json")


def load_memory(sig: str) -> dict[str, Any]:
    """The memory doc for a signature ({} if none/unreadable)."""
    if not sig:
        return {}
    try:
        with open(_path_for(sig), "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(sig: str, doc: dict[str, Any]) -> None:
    os.makedirs(_memory_dir(), exist_ok=True)
    tmp = _path_for(sig) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, default=str)
    os.replace(tmp, _path_for(sig))


def _base_doc(sig: str) -> dict[str, Any]:
    return {
        "signature": sig,
        "runs": [],
        "leakage_columns": {},        # raw column -> times confirmed leaky
        "confirmed_hypotheses": [],
        "refuted_hypotheses": [],
        "winning_models": {},         # model name -> wins
    }


def record_run_outcome(
    sig: str,
    run_id: str,
    *,
    grade: str | None = None,
    leaked_columns: list[str] | None = None,
    winner_model: str | None = None,
    final_score: float | None = None,
) -> None:
    """Trust-gate writer: run outcome + confirmed leakage (raw column names)."""
    if not sig:
        return
    try:
        doc = load_memory(sig) or _base_doc(sig)
        doc.setdefault("leakage_columns", {})
        doc.setdefault("winning_models", {})
        doc.setdefault("runs", [])
        for c in (leaked_columns or []):
            if isinstance(c, str) and c:
                doc["leakage_columns"][c] = int(doc["leakage_columns"].get(c, 0)) + 1
        if isinstance(winner_model, str) and winner_model:
            doc["winning_models"][winner_model] = int(doc["winning_models"].get(winner_model, 0)) + 1
        doc["runs"] = (doc["runs"] + [{
            "run_id": run_id,
            "at": datetime.now(timezone.utc).isoformat(),
            "grade": grade,
            "winner_model": winner_model,
            "final_score": final_score,
        }])[-_MAX_RUNS:]
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save(sig, doc)
    except Exception as e:  # memory must never break a run
        _log.warning("run_memory_write_failed", sig=sig, error=str(e)[:200])


def record_investigation(sig: str, run_id: str, verdicts: list[dict]) -> None:
    """Hypothesis-loop writer: persist confirmed/refuted findings (deduped)."""
    if not sig:
        return
    try:
        doc = load_memory(sig) or _base_doc(sig)
        for key, verdict in (("confirmed_hypotheses", "confirmed"),
                             ("refuted_hypotheses", "refuted")):
            doc.setdefault(key, [])
            seen = {f.get("statement") for f in doc[key] if isinstance(f, dict)}
            for v in verdicts:
                if not isinstance(v, dict) or v.get("verdict") != verdict:
                    continue
                stmt = v.get("statement")
                if not isinstance(stmt, str) or not stmt or stmt in seen:
                    continue
                seen.add(stmt)
                doc[key].append({
                    "statement": stmt,
                    "primitive": v.get("primitive"),
                    "observed": v.get("observed"),
                    "run_id": run_id,
                    "at": datetime.now(timezone.utc).isoformat(),
                })
            doc[key] = doc[key][-_MAX_FINDINGS:]
        doc["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save(sig, doc)
    except Exception as e:
        _log.warning("run_memory_write_failed", sig=sig, error=str(e)[:200])


def memory_brief(mem: dict[str, Any], max_chars: int = 1200) -> str:
    """Compact plain-text brief of prior findings for LLM prompts ('' if empty)."""
    if not mem:
        return ""
    lines: list[str] = []
    leaks = mem.get("leakage_columns") or {}
    if leaks:
        top = sorted(leaks.items(), key=lambda kv: -kv[1])[:8]
        lines.append("Previously CONFIRMED leaky columns on this dataset: "
                     + ", ".join(f"{c} (x{n})" for c, n in top))
    wins = mem.get("winning_models") or {}
    if wins:
        top = sorted(wins.items(), key=lambda kv: -kv[1])[:5]
        lines.append("Models that won on this dataset before: "
                     + ", ".join(f"{m} (x{n})" for m, n in top))
    conf = [f for f in (mem.get("confirmed_hypotheses") or []) if isinstance(f, dict)]
    if conf:
        lines.append("Previously confirmed findings:")
        lines += [f"- {f.get('statement')}" for f in conf[-6:]]
    refu = [f for f in (mem.get("refuted_hypotheses") or []) if isinstance(f, dict)]
    if refu:
        lines.append("Previously REFUTED (do not re-test identically):")
        lines += [f"- {f.get('statement')}" for f in refu[-4:]]
    text = "\n".join(lines)
    return text[:max_chars]


def signature_for_run(run_id: str, fallback_path: str = "") -> str:
    """Signature from the run's ORIGINAL upload (dataset_path gets repointed at
    train.csv mid-pipeline; both have the same header, but prefer the upload)."""
    original = os.path.join(settings.DATA_DIR, run_id, "dataset.csv")
    if os.path.exists(original):
        return dataset_signature(original)
    return dataset_signature(fallback_path) if fallback_path else ""
