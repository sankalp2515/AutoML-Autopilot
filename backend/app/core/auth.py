"""Multi-tenancy + auth (Phase 4) — opt-in, safe by default.

When ``TENANT_API_KEYS`` is empty the system runs in single-tenant "public" mode:
``resolve_tenant`` returns "public", ownership checks always pass — i.e. ZERO
behavior change from the pre-Phase-4 system. When it's set (JSON api-key→tenant),
each run is owned by the calling tenant and cross-tenant access 404s.

Enforcement is applied UNIFORMLY as a router-level dependency
(``enforce_run_ownership``) so every run-scoped endpoint is covered — there is no
per-endpoint hole to forget. Endpoints that create/list runs use ``current_tenant``.
"""

from __future__ import annotations

import json
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db

PUBLIC_TENANT = "public"


@lru_cache
def _key_map() -> dict[str, str]:
    """Parse TENANT_API_KEYS (JSON) → {api_key: tenant_id}. Empty/invalid = {}."""
    raw = (settings.TENANT_API_KEYS or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


_jwks_clients: dict = {}   # url → cached PyJWKClient (fetches + caches public keys)


def _supabase_enabled() -> bool:
    # Legacy projects: shared HS256 secret. New projects: asymmetric keys via JWKS
    # (only SUPABASE_URL needed). Either signal turns Supabase auth on.
    return bool(settings.SUPABASE_JWT_SECRET) or bool(settings.SUPABASE_URL)


def tenant_mode_enabled() -> bool:
    # Auth is OFF unless explicitly enabled — so Supabase/API keys sitting in .env
    # never accidentally produce a 401. When AUTH_ENABLED is false the whole system
    # runs in public mode and no endpoint ever requires a token.
    if not settings.AUTH_ENABLED:
        return False
    return bool(_key_map()) or _supabase_enabled()


def _verify_supabase_jwt(token: str) -> str | None:
    """Verify a Supabase access token and return the user id (`sub`), or None.

    Supports BOTH signing schemes: HS256 (legacy shared JWT secret) and the newer
    asymmetric RS256/ES256 keys, verified against the project's JWKS endpoint.
    """
    if not token or not _supabase_enabled():
        return None
    try:
        import jwt  # PyJWT
        alg = jwt.get_unverified_header(token).get("alg", "HS256")

        if alg == "HS256" and settings.SUPABASE_JWT_SECRET:
            claims = jwt.decode(token, settings.SUPABASE_JWT_SECRET, algorithms=["HS256"],
                                audience=settings.SUPABASE_JWT_AUD)
        elif alg in ("RS256", "ES256") and settings.SUPABASE_URL:
            jwks_url = settings.SUPABASE_URL.rstrip("/") + "/auth/v1/.well-known/jwks.json"
            client = _jwks_clients.get(jwks_url)
            if client is None:
                client = jwt.PyJWKClient(jwks_url)
                _jwks_clients[jwks_url] = client
            signing_key = client.get_signing_key_from_jwt(token)
            claims = jwt.decode(token, signing_key.key, algorithms=[alg],
                                audience=settings.SUPABASE_JWT_AUD)
        else:
            return None

        sub = claims.get("sub")
        return str(sub) if sub else None
    except Exception:
        return None


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def resolve_tenant(api_key: str | None, authorization: str | None = None) -> str | None:
    """Resolve the caller's tenant from a Supabase Bearer token OR an X-API-Key.
    Public mode (nothing configured) → PUBLIC_TENANT. Otherwise None means
    unauthenticated (caller should get 401)."""
    if not tenant_mode_enabled():
        return PUBLIC_TENANT
    # Supabase logged-in user → tenant "user:<uuid>"
    sub = _verify_supabase_jwt(_bearer(authorization))
    if sub:
        return f"user:{sub}"
    # Static service api-key → mapped tenant
    if api_key and _key_map().get(api_key):
        return _key_map()[api_key]
    return None


async def current_tenant(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> str:
    """Dependency: the calling tenant, or 401 in tenant mode without valid creds."""
    tenant = resolve_tenant(x_api_key, authorization)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Authentication required (Bearer token or X-API-Key)")
    return tenant


async def enforce_run_ownership(
    request: Request,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Router-level guard: if the path carries a run id, the run must belong to the
    caller's tenant (else 404 — we don't reveal another tenant's run exists).
    No-op in public mode."""
    if not tenant_mode_enabled():
        return
    tenant = resolve_tenant(x_api_key, authorization)
    if tenant is None:
        raise HTTPException(status_code=401, detail="Authentication required (Bearer token or X-API-Key)")

    run_id = request.path_params.get("run_id")
    if not run_id:
        return  # collection-level endpoint (create/list) handles scoping itself

    from app.models.run import Run
    result = await db.execute(select(Run.tenant_id).where(Run.id == run_id))
    owner = result.scalar_one_or_none()
    if owner is not None and owner != tenant:
        raise HTTPException(status_code=404, detail="Run not found")
