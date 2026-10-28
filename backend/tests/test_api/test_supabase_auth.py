"""Supabase JWT → tenant resolution (Phase 5 auth).

Skips if PyJWT isn't installed locally (it's in requirements; runs in-container).
"""

import time

import pytest

jwt = pytest.importorskip("jwt")

from app.config import settings
from app.core import auth

_SECRET = "test-supabase-jwt-secret"


@pytest.fixture
def supabase_on(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", _SECRET)
    monkeypatch.setattr(settings, "TENANT_API_KEYS", "")
    auth._key_map.cache_clear()
    yield
    auth._key_map.cache_clear()


def _token(sub="user-123", aud="authenticated", exp_delta=3600, secret=_SECRET):
    return jwt.encode(
        {"sub": sub, "aud": aud, "exp": int(time.time()) + exp_delta},
        secret, algorithm="HS256",
    )


def test_valid_token_resolves_to_user_tenant(supabase_on):
    t = _token(sub="abc-xyz")
    assert auth.resolve_tenant(None, f"Bearer {t}") == "user:abc-xyz"


def test_expired_token_rejected(supabase_on):
    t = _token(exp_delta=-10)
    assert auth.resolve_tenant(None, f"Bearer {t}") is None


def test_wrong_secret_rejected(supabase_on):
    t = _token(secret="attacker-secret")
    assert auth.resolve_tenant(None, f"Bearer {t}") is None


def test_wrong_audience_rejected(supabase_on):
    t = _token(aud="not-authenticated")
    assert auth.resolve_tenant(None, f"Bearer {t}") is None


def test_missing_token_in_tenant_mode_is_unauthenticated(supabase_on):
    assert auth.resolve_tenant(None, None) is None


def test_supabase_disabled_is_public_mode():
    auth._key_map.cache_clear()
    assert auth.resolve_tenant(None, "Bearer whatever") == "public"


def test_rs256_via_jwks_for_new_projects(monkeypatch):
    """New Supabase projects sign with asymmetric keys (RS256) — verified against
    the JWKS endpoint, not a shared secret."""
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", "")
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://proj.supabase.co")
    auth._key_map.cache_clear()
    auth._jwks_clients.clear()

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    token = jwt.encode({"sub": "user-rs-1", "aud": "authenticated",
                        "exp": int(time.time()) + 3600}, priv_pem, algorithm="RS256")

    class _FakeSigningKey:
        key = private_key.public_key()

    class _FakeJWKClient:
        def __init__(self, url): pass
        def get_signing_key_from_jwt(self, _t): return _FakeSigningKey()

    monkeypatch.setattr(jwt, "PyJWKClient", _FakeJWKClient)
    assert auth.resolve_tenant(None, f"Bearer {token}") == "user:user-rs-1"
