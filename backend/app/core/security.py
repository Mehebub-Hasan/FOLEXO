"""Security helpers.

Production authentication is Supabase Auth (ADR-004): clients present
Supabase-issued HS256 JWTs, verified here against SUPABASE_JWT_SECRET.
The password-hashing utilities are a standard-library placeholder (PBKDF2)
kept for local fixtures/testing only — no production flow sets a password.

Phase 3 will wrap ``decode_supabase_token`` in a FastAPI dependency that
resolves the caller's organization memberships.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.core.config import settings

_PBKDF2_ITERATIONS = 240_000
_SALT_BYTES = 16


# ── Password hashing (placeholder — see module docstring) ────────────────


def hash_password(password: str) -> str:
    salt = secrets.token_hex(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        algorithm, iterations, salt, digest_hex = hashed.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


# ── JWT utilities (Supabase tokens) ──────────────────────────────────────


def _jwt_secret() -> str:
    if settings.supabase_jwt_secret:
        return settings.supabase_jwt_secret
    if settings.is_production:
        raise RuntimeError("SUPABASE_JWT_SECRET must be set in production")
    return "dev-insecure-jwt-secret-do-not-use-in-production"


def create_access_token(
    subject: str,
    extra_claims: dict[str, Any] | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Mint a token in the same shape Supabase uses (test/dev convenience)."""
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes)),
        "role": "authenticated",
    }
    if settings.jwt_audience:
        payload["aud"] = settings.jwt_audience
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _jwt_secret(), algorithm=settings.jwt_algorithm)


def decode_supabase_token(token: str) -> dict[str, Any]:
    """Verify a Supabase access token locally (signature + expiry).

    Returns the claim set (``sub`` is the auth user id). Raises ``ValueError``
    with a client-safe message on any validation failure — never leak the
    underlying exception to callers.
    """
    try:
        return jwt.decode(
            token,
            _jwt_secret(),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            options={"require": ["exp", "sub"]},
        )
    except jwt.InvalidTokenError as exc:
        raise ValueError("invalid or expired token") from exc
