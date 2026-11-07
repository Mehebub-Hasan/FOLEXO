"""Security helpers.

Production authentication is Supabase Auth (ADR-004): clients present
Supabase-issued JWTs, verified here locally — signature, expiry, audience and
issuer — with no network call per request.

- HS256 (legacy Supabase signing): verified with ``SUPABASE_JWT_SECRET``.
- RS256 (Supabase JWT signing keys): verified with ``JWT_PUBLIC_KEY`` (PEM).

The password-hashing utilities are a standard-library placeholder (PBKDF2)
kept for local fixtures/testing only — no production flow sets a password.
``verify_supabase_token`` is consumed by the auth dependency in
``app/api/deps.py`` (Phase 2).
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from pydantic import BaseModel

from app.core.config import settings

_PBKDF2_ITERATIONS = 240_000
_SALT_BYTES = 16


class UserPayload(BaseModel):
    """Verified identity extracted from a Supabase access token."""

    id: uuid.UUID
    email: str


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


def _jwt_key() -> str:
    if settings.jwt_algorithm == "RS256":
        if not settings.jwt_public_key:
            raise RuntimeError("JWT_PUBLIC_KEY (PEM) is required when JWT_ALGORITHM=RS256")
        return settings.jwt_public_key
    if settings.supabase_jwt_secret:
        return settings.supabase_jwt_secret
    if settings.is_production:
        raise RuntimeError("SUPABASE_JWT_SECRET must be set in production")
    return "dev-insecure-jwt-secret-do-not-use-in-production"


def create_access_token(
    subject: str,
    email: str | None = None,
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
    if email:
        payload["email"] = email
    if settings.jwt_audience:
        payload["aud"] = settings.jwt_audience
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _jwt_key(), algorithm=settings.jwt_algorithm)


def verify_supabase_token(token: str) -> UserPayload:
    """Verify a Supabase access token and return its identity.

    Validates signature (per configured algorithm), expiration (with a small
    clock-skew leeway), audience and issuer when configured. Raises
    ``ValueError`` with a client-safe message on any failure — the HTTP layer
    translates that into 401; the underlying exception never leaks.
    """
    decode_kwargs: dict[str, Any] = {
        "algorithms": [settings.jwt_algorithm],
        "options": {"require": ["exp", "sub"]},
        "leeway": settings.jwt_leeway_seconds,
    }
    if settings.jwt_audience:
        decode_kwargs["audience"] = settings.jwt_audience
    issuer = settings.effective_jwt_issuer
    if issuer:
        decode_kwargs["issuer"] = issuer

    try:
        claims = jwt.decode(token, _jwt_key(), **decode_kwargs)
    except jwt.ExpiredSignatureError as exc:
        raise ValueError("token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise ValueError("invalid or expired token") from exc

    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("token subject is not a valid user id") from exc

    return UserPayload(id=user_id, email=str(claims.get("email") or ""))
