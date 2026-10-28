"""Guardrails — validate inputs INTO the system and decisions OUT of the LLM.

The pipeline trusts LLM output as *decisions* (per the doctrine), but a malformed
decision can poison the whole run (e.g. a metric that doesn't exist for the task
→ silent NaN scorer; an out-of-range threshold → crash). These guardrails clamp
LLM output to valid values BEFORE it flows downstream, and sanitize free-text user
input. They never raise — they FIX and report, so the run continues safely.
"""

from __future__ import annotations

import re

from app.core import metric_registry

# Prompt-injection patterns to detect in the free-text goal. The goal is only ever
# used as DATA to frame an ML problem (the LLM has no tools/data access and its
# output is structurally validated), so injection is low-risk — but detecting and
# neutralizing it is the expected AI-engineering practice.
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(the\s+)?(previous|above|prior)\s+instructions",
    r"disregard\s+(the\s+)?(above|previous|prior)",
    r"you\s+are\s+now\b",
    r"\bact\s+as\b",
    r"system\s+prompt",
    r"(reveal|print|show|repeat)\s+(your|the)\s+(instructions|prompt|system)",
    r"developer\s+mode",
    r"\bjailbreak\b",
    r"</?(system|instructions?|assistant)>",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

_VALID_TASKS = {
    "binary_classification", "multiclass_classification",
    "multilabel_classification", "regression",
}
_MAX_GOAL_LEN = 2000


def sanitize_user_goal(goal: str) -> str:
    """Strip control characters (keep newlines/tabs), collapse whitespace, cap length.
    Defends against junk/binary paste and absurdly long prompts inflating tokens."""
    if not goal:
        return ""
    cleaned = "".join(c for c in goal if c in ("\n", "\t") or ord(c) >= 32)
    cleaned = " ".join(cleaned.split())
    return cleaned[:_MAX_GOAL_LEN]


def scan_for_injection(text: str) -> list[str]:
    """Return the distinct prompt-injection phrases detected in `text` (empty = clean)."""
    if not text:
        return []
    return sorted({m.group(0).lower() for m in _INJECTION_RE.finditer(text)})


def neutralize_injection(text: str) -> tuple[str, list[str]]:
    """Strip detected injection phrases, returning (cleaned_text, flags)."""
    flags = scan_for_injection(text)
    if not flags:
        return text, []
    cleaned = _INJECTION_RE.sub("[removed]", text)
    return " ".join(cleaned.split()), flags


def validate_and_fix_framing(framing: dict, columns: list[str]) -> tuple[dict, list[str]]:
    """Clamp the problem-framer's output to valid values. Returns (fixed, notes).

    - task_type must be one of the known tasks (else → binary_classification)
    - primary_metric must be valid FOR that task (else → the task's default scorer)
    - good_enough_threshold must be in [0, 1] (else → 0.8)
    - target_column should exist in the dataset (reported; not auto-fixed)
    """
    f = dict(framing)
    notes: list[str] = []

    task = f.get("task_type")
    if task not in _VALID_TASKS:
        notes.append(f"task_type '{task}' is invalid → defaulting to binary_classification")
        task = "binary_classification"
        f["task_type"] = task

    metric = f.get("primary_metric")
    if metric not in metric_registry.allowed_for(task):
        fixed = metric_registry.default_for(task)
        notes.append(f"primary_metric '{metric}' is not valid for {task} → {fixed}")
        f["primary_metric"] = fixed

    th = f.get("good_enough_threshold")
    if not isinstance(th, (int, float)) or isinstance(th, bool) or not (0.0 <= float(th) <= 1.0):
        notes.append(f"good_enough_threshold '{th}' out of [0,1] → 0.8")
        f["good_enough_threshold"] = 0.8

    tgt = f.get("target_column")
    if task != "multilabel_classification" and columns and tgt not in columns:
        # Can't safely guess the target — surface it loudly; baseline will error clearly.
        notes.append(f"target_column '{tgt}' was not found in dataset columns {columns[:8]}")

    # ── Phase 3.5: rare-event budget + business objective ────────────────────
    # The at-k metrics are meaningless without a budget, and a budget outside
    # (0, 1] would make the scorer target zero rows or the whole population.
    if metric_registry.is_custom_k(f.get("primary_metric", "")):
        k = f.get("rare_event_k")
        if not isinstance(k, (int, float)) or isinstance(k, bool) or not (0.0 < float(k) <= 1.0):
            notes.append(f"rare_event_k '{k}' is not a fraction in (0,1] → "
                         f"{metric_registry.DEFAULT_K}")
            f["rare_event_k"] = metric_registry.DEFAULT_K
        else:
            f["rare_event_k"] = float(k)
    elif "rare_event_k" in f:
        f.pop("rare_event_k")      # irrelevant to a non-at-k metric; don't carry noise

    # An objective is optional. When present it must survive BusinessObjective's
    # tolerant parser AND be non-degenerate, or the threshold optimizer would
    # return an arbitrary operating point dressed up as a recommendation.
    obj_raw = f.get("business_objective")
    if obj_raw is not None:
        from app.core.business_objective import BusinessObjective
        obj = BusinessObjective.from_dict(obj_raw)
        if not obj.is_meaningful:
            notes.append("business_objective values are all equal (degenerate) → dropped; "
                         "no threshold is better than any other under it")
            f["business_objective"] = None
        else:
            f["business_objective"] = {**obj.to_dict(), "source": "llm"}

    return f, notes
