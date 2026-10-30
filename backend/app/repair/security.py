"""Security helpers for v5 (BE-17, BE-21): secret redaction, repository URL guard, pinned installs."""
from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urlsplit

from app.config import settings

_SECRETS = re.compile(
    r"(nvapi-[A-Za-z0-9_-]{20,}|tvly-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|"
    r"github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|secret|password|token)\s*[=:]\s*['\"]?[A-Za-z0-9_\-/+=]{12,}))")


def redact(text: str) -> str:
    """Remove credential-shaped strings before text reaches a model, a log or a receipt."""
    return _SECRETS.sub("[REDACTED]", text or "")


class UnsafeSource(ValueError):
    pass


def check_repo_url(url: str) -> str:
    """HTTPS only, allow-listed hosts, no credentials, no private or local addresses (SSRF)."""
    try:
        u = urlsplit(url.strip());port=u.port
    except ValueError as exc:
        raise UnsafeSource("use a valid public HTTPS repository URL") from exc
    if u.scheme != "https" or not u.hostname or u.username or u.password or port not in (None, 443) or u.query or u.fragment:
        raise UnsafeSource("use a public https:// repository URL without credentials")
    host = u.hostname.lower()
    allowed = {h.strip().lower() for h in settings.REPAIR_ALLOWED_GIT_HOSTS.split(",") if h.strip()}
    if host not in allowed:
        raise UnsafeSource(f"repository host must be one of: {', '.join(sorted(allowed))}")
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(host, 443)}
    except OSError as e:
        raise UnsafeSource("repository host does not resolve") from e
    for a in addrs:
        ip = ipaddress.ip_address(a)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise UnsafeSource("repository host resolves to a private address")
    if not re.fullmatch(r"/[\w.-]+/[\w.-]+(\.git)?/?", u.path):
        raise UnsafeSource("use the repository root URL, e.g. https://github.com/owner/name")
    return f"https://{host}{u.path.rstrip('/')}"


def unpinned_requirements(repo: Path) -> list[str]:
    """Lines in requirements.txt that are not exact pins (`name==version`)."""
    req = repo / "requirements.txt"
    if not req.is_file():
        return []
    bad = []
    for line in req.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.split("#", 1)[0].strip()
        if not s:
            continue
        if s.startswith(("-", "git+", "http")) or not re.fullmatch(r"[A-Za-z0-9_.\-\[\],]+==[A-Za-z0-9_.+\-]+(\s*;.*)?", s):
            bad.append(s)
    return bad
