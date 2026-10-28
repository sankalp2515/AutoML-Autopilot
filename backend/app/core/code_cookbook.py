"""
Code Cookbook (E2) — a JSONL store of code that has actually run successfully,
retrieved by EXACT tags + keyword overlap. NO embeddings/vectors (deterministic,
auditable, no hallucinated relevance).

Each line is one entry:
  {id, agent_role, task_type, tags[], signature, code, result_keys[],
   provenance: "generated"|"repaired"|"template", checksum, success_count, fail_count}

Primary use: when a templated step FAILS, retrieve prior fixes for this
agent_role + task + error-signature so the LLM can adapt a proven fix rather than
write from scratch. Successful fixes are recorded back so the system gets more
robust over time — without paying any LLM cost on the happy (template) path.
"""

import hashlib
import json
import re
import threading
import time
from pathlib import Path

# In the repo (backend is volume-mounted) so the cookbook is version-controllable.
COOKBOOK_PATH = Path(__file__).resolve().parents[2] / "cookbook" / "code_cookbook.jsonl"
_LOCK = threading.Lock()

_WORD = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]+")


def _checksum(code: str) -> str:
    return hashlib.md5(code.encode("utf-8")).hexdigest()


def _keywords(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "") if len(w) > 2}


def _load() -> list[dict]:
    if not COOKBOOK_PATH.exists():
        return []
    out = []
    for line in COOKBOOK_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _write_all(entries: list[dict]) -> None:
    COOKBOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with COOKBOOK_PATH.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def retrieve(agent_role: str, task_type: str, tags: list[str],
             keywords: str = "", k: int = 3) -> list[dict]:
    """Top-k matching snippets. Filter by agent_role (+ task family), rank by
    tag overlap then keyword overlap then success_count. Fully deterministic."""
    want_tags = set(tags or [])
    want_kw = _keywords(keywords)
    scored = []
    for e in _load():
        if e.get("agent_role") != agent_role:
            continue
        if e.get("task_type") not in (task_type, "any", None):
            continue
        tag_overlap = len(want_tags & set(e.get("tags", [])))
        kw_overlap = len(want_kw & _keywords(e.get("signature", "") + " " + e.get("code", "")))
        # demote snippets that have failed more than they've helped
        health = e.get("success_count", 0) - e.get("fail_count", 0)
        if health < 0:
            continue
        score = (tag_overlap * 10) + kw_overlap + min(health, 5)
        if score > 0:
            scored.append((score, e))
    scored.sort(key=lambda t: -t[0])
    return [e for _, e in scored[:k]]


def record_success(agent_role: str, task_type: str, tags: list[str],
                   signature: str, code: str, result_keys: list[str],
                   provenance: str = "generated") -> str:
    """Upsert a working snippet by checksum (increment success_count if seen)."""
    cs = _checksum(code)
    with _LOCK:
        entries = _load()
        for e in entries:
            if e.get("checksum") == cs:
                e["success_count"] = e.get("success_count", 0) + 1
                e["last_used_at"] = time.time()
                _write_all(entries)
                return cs
        entries.append({
            "id": cs[:12], "agent_role": agent_role, "task_type": task_type,
            "tags": tags or [], "signature": signature[:500], "code": code,
            "result_keys": result_keys or [], "provenance": provenance,
            "checksum": cs, "success_count": 1, "fail_count": 0,
            "created_at": time.time(), "last_used_at": time.time(),
        })
        # cap store size per (agent, task) — keep the healthiest 50
        same = [e for e in entries if e["agent_role"] == agent_role and e["task_type"] == task_type]
        if len(same) > 50:
            same.sort(key=lambda e: -(e.get("success_count", 0) - e.get("fail_count", 0)))
            keep = set(id(e) for e in same[:50])
            entries = [e for e in entries
                       if not (e["agent_role"] == agent_role and e["task_type"] == task_type)
                       or id(e) in keep]
        _write_all(entries)
    return cs


def record_failure(checksum: str) -> None:
    with _LOCK:
        entries = _load()
        for e in entries:
            if e.get("checksum") == checksum:
                e["fail_count"] = e.get("fail_count", 0) + 1
                _write_all(entries)
                return


def all_entries() -> list[dict]:
    """For the governance/inspection endpoint (E4)."""
    return _load()
