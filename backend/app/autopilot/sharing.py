"""Read-only bearer capabilities. Tokens grant one terminal run, never a tenant."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from pathlib import Path

from app.config import settings


def redact_path(path: str) -> str:
    return re.sub(r"(/api/v1/autopilot/shared/)[^/?#]+", r"\1:token", path)


class CapabilityLogFilter(logging.Filter):
    """Uvicorn access logs must not record read-only bearer capabilities."""
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            args = list(record.args)
            args[2] = redact_path(str(args[2]))
            record.args = tuple(args)
        return True


def install_access_log_filter() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, CapabilityLogFilter) for f in logger.filters):
        logger.addFilter(CapabilityLogFilter())


def _key() -> bytes:
    configured = settings.AUTOPILOT_SHARE_SECRET
    if configured:
        return configured.encode()
    # Persist across restarts; shared DATA_DIR is required for multiple API replicas.
    p = Path(settings.DATA_DIR) / ".share-key"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "wb") as f:
            f.write(secrets.token_bytes(32))
    key = p.read_bytes()
    if len(key) < 32:
        raise RuntimeError("share key unavailable")
    return key


def sign(run_id: str, lifetime: int = 86400) -> tuple[str, int]:
    expires = int(time.time()) + lifetime
    payload = f"{run_id}.{expires}"
    return f"{payload}.{hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()}", expires


def resolve(token: str) -> str:
    if not re.fullmatch(r"[0-9a-f-]{36}\.[0-9]{10}\.[0-9a-f]{64}", token):
        raise ValueError("Invalid or expired share link")
    run_id, expires, signature = token.split(".")
    expected = hmac.new(_key(), f"{run_id}.{expires}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected) or int(expires) <= time.time():
        raise ValueError("Invalid or expired share link")
    return run_id
