"""Runtime-editable config overlay — backs the Settings control panel.

Base configuration comes from env via pydantic ``Settings``. This module adds a
writable overlay (``runtime_config.json``) that takes precedence for a curated,
safe set of keys. On update we mutate the live ``settings`` singleton so
live-readable values (DL_ENABLED, LLM_PROVIDER, keys, limits…) take effect
immediately; keys flagged ``restart=True`` only fully apply on the next start.

Secrets are write-only over the API: their values are never returned, only an
``is_set`` boolean, and a blank incoming value never overwrites a stored secret.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.config import settings

_OVERLAY_PATH = Path(
    os.getenv("RUNTIME_CONFIG_PATH", str(Path(__file__).resolve().parents[2] / "runtime_config.json"))
)

# key → {type, group, label, help, restart, secret}. Only keys listed here are
# editable; everything else is immutable from the UI.
EDITABLE_FIELDS: dict[str, dict[str, Any]] = {
    # ── Feature flags ─────────────────────────────────────────────────────────
    "DL_ENABLED": {"type": "bool", "group": "Feature flags", "restart": False,
                   "label": "Deep learning (TorchMLP)",
                   "help": "Let the model scout pick PyTorch models. Requires the sandbox image rebuilt with torch."},
    "PARALLEL_CANDIDATES": {"type": "bool", "group": "Feature flags", "restart": False,
                            "label": "Parallel candidate scouting",
                            "help": "Cross-validate model candidates in concurrent sandbox calls "
                                    "(asyncio, not Ray). Off = serial scouting."},
    "USE_RAY": {"type": "bool", "group": "Feature flags", "restart": True,
                "label": "Ray parallelism (research toolkit)",
                "help": "Fan out the research toolkit's CV sweeps across a Ray cluster. The main "
                        "AutoML pipeline trains inside the sandbox and is not affected by this flag."},
    "USE_JOB_QUEUE": {"type": "bool", "group": "Feature flags", "restart": True,
                      "label": "Durable job queue (arq worker)",
                      "help": "Route runs through a Redis worker instead of in-process. Restart required."},
    "LLM_CACHE_ENABLED": {"type": "bool", "group": "Feature flags", "restart": False,
                          "label": "LLM completion cache", "help": "Cache identical completions to cut cost."},
    "INJECTION_GUARD_STRICT": {"type": "bool", "group": "Feature flags", "restart": False,
                               "label": "Strict prompt-injection guard",
                               "help": "Reject goals with detected injection (else log + strip)."},
    # ── LLM providers ─────────────────────────────────────────────────────────
    "LLM_PROVIDER": {"type": "str", "group": "LLM providers", "restart": False,
                     "label": "Primary provider", "help": "gemini | groq | openrouter | deepseek | ollama | anthropic"},
    "LLM_FALLBACK_CHAIN": {"type": "str", "group": "LLM providers", "restart": False,
                           "label": "Fallback chain", "help": "Comma-separated providers, tried left to right."},
    "GEMINI_API_KEY": {"type": "str", "group": "LLM providers", "restart": False, "secret": True, "label": "Gemini API key"},
    "GROQ_API_KEY": {"type": "str", "group": "LLM providers", "restart": False, "secret": True, "label": "Groq API key"},
    "OPENROUTER_API_KEY": {"type": "str", "group": "LLM providers", "restart": False, "secret": True, "label": "OpenRouter API key"},
    "DEEPSEEK_API_KEY": {"type": "str", "group": "LLM providers", "restart": False, "secret": True, "label": "DeepSeek API key"},
    "ANTHROPIC_API_KEY": {"type": "str", "group": "LLM providers", "restart": False, "secret": True, "label": "Anthropic API key"},
    # ── Run limits ────────────────────────────────────────────────────────────
    "MAX_ITERATIONS": {"type": "int", "group": "Run limits", "restart": False, "label": "Max improvement iterations"},
    "MAX_RUN_SECONDS": {"type": "int", "group": "Run limits", "restart": False, "label": "Max run seconds (0 = no cap)"},
    "MAX_AGENT_SECONDS": {"type": "int", "group": "Run limits", "restart": False,
                          "label": "Max seconds per agent (0 = no cap)"},
    "MAX_TRAIN_ROWS": {"type": "int", "group": "Run limits", "restart": False,
                       "label": "Max training rows (0 = no cap)",
                       "help": "Large datasets are stratified-sampled to this at the split; the holdout is never sampled."},
    "RAY_ADDRESS": {"type": "str", "group": "Run limits", "restart": True,
                    "label": "Ray cluster address (empty = local)",
                    "help": "S3 seam: 'ray://head:10001' or 'auto' joins a cluster; parallel work fans out."},
    "DRIFT_MONITOR_INTERVAL_S": {"type": "int", "group": "Run limits", "restart": True,
                                 "label": "Drift monitor interval s (0 = off)",
                                 "help": "Checks active deployments; drift auto-launches ONE challenger/day."},
    "MAX_TRAIN_ROWS_HARD": {"type": "int", "group": "Run limits", "restart": False,
                            "label": "Adaptive cap ceiling (0 = never escalate)",
                            "help": "If the learning curve is still climbing at the cap, it escalates up to this."},
    "STREAMING_THRESHOLD_ROWS": {"type": "int", "group": "Run limits", "restart": False,
                                 "label": "Streaming threshold rows",
                                 "help": "Above this many rows, training switches to out-of-core streaming (every row)."},
    "IMPROVEMENT_THRESHOLD": {"type": "float", "group": "Run limits", "restart": False, "label": "Min improvement to iterate"},
    "RATE_LIMIT_PER_MIN": {"type": "int", "group": "Run limits", "restart": False, "label": "API rate limit / min (0 = off)"},
    "TENANT_BUDGET_USD": {"type": "float", "group": "Run limits", "restart": False, "label": "Per-tenant LLM budget USD (0 = ∞)"},
    # ── Auth & tenancy ────────────────────────────────────────────────────────
    "AUTH_ENABLED": {"type": "bool", "group": "Auth & tenancy", "restart": False,
                     "label": "Require authentication",
                     "help": "Turn on the auth wall (Supabase / API keys). Off = public mode."},
    "QUOTA_MAX_ACTIVE_RUNS_PER_TENANT": {"type": "int", "group": "Auth & tenancy", "restart": False,
                                         "label": "Max active runs / tenant (0 = ∞)"},
    "SUPABASE_URL": {"type": "str", "group": "Auth & tenancy", "restart": False, "label": "Supabase URL"},
    "SUPABASE_JWT_SECRET": {"type": "str", "group": "Auth & tenancy", "restart": False, "secret": True,
                            "label": "Supabase JWT secret (HS256)"},
    "TENANT_API_KEYS": {"type": "str", "group": "Auth & tenancy", "restart": False, "secret": True,
                        "label": "Tenant API keys (JSON map)"},
    "API_KEY": {"type": "str", "group": "Auth & tenancy", "restart": False, "secret": True,
                "label": "Admin API key (guards this page)"},
}

# Stable group order for the UI.
GROUPS: list[str] = []
for _m in EDITABLE_FIELDS.values():
    if _m["group"] not in GROUPS:
        GROUPS.append(_m["group"])


def _coerce(field: str, raw: Any) -> Any:
    t = EDITABLE_FIELDS[field]["type"]
    if t == "bool":
        return raw if isinstance(raw, bool) else str(raw).strip().lower() in ("1", "true", "yes", "on")
    if t == "int":
        return int(raw)
    if t == "float":
        return float(raw)
    return str(raw)


def _read() -> dict[str, Any]:
    try:
        return json.loads(_OVERLAY_PATH.read_text())
    except Exception:
        return {}


def _post_apply() -> None:
    # TENANT_API_KEYS is parsed behind an lru_cache — bust it so changes apply.
    try:
        from app.core import auth
        auth._key_map.cache_clear()
    except Exception:
        pass


def apply_overlay() -> None:
    """Load the persisted overlay over the live settings (call once at startup)."""
    data = _read()
    for key, val in data.items():
        if key in EDITABLE_FIELDS:
            try:
                setattr(settings, key, _coerce(key, val))
            except Exception:
                pass
    _post_apply()


def describe() -> list[dict[str, Any]]:
    """Current editable config for the UI; secret values are masked."""
    out: list[dict[str, Any]] = []
    for key, meta in EDITABLE_FIELDS.items():
        val = getattr(settings, key, None)
        secret = meta.get("secret", False)
        entry: dict[str, Any] = {
            "key": key, "label": meta.get("label", key), "group": meta["group"],
            "type": meta["type"], "restart": meta.get("restart", False),
            "secret": secret, "help": meta.get("help", ""),
        }
        if secret:
            entry["value"] = ""
            entry["is_set"] = bool(val)
        else:
            entry["value"] = val
        out.append(entry)
    return out


def update(changes: dict[str, Any]) -> dict[str, Any]:
    """Validate + apply changes to live settings, persist the overlay."""
    overlay = _read()
    applied: list[str] = []
    restart: list[str] = []
    for key, raw in changes.items():
        meta = EDITABLE_FIELDS.get(key)
        if meta is None:
            continue
        if meta.get("secret") and (raw is None or str(raw) == ""):
            continue  # never blank-out a stored secret
        try:
            val = _coerce(key, raw)
        except (ValueError, TypeError):
            continue
        setattr(settings, key, val)
        overlay[key] = val
        applied.append(key)
        if meta.get("restart"):
            restart.append(key)
    try:
        _OVERLAY_PATH.write_text(json.dumps(overlay, indent=2))
    except Exception:
        pass
    _post_apply()
    return {"applied": applied, "restart_required": restart}
