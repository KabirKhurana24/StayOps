"""Supabase access-token verification.

Supabase projects sign access tokens either with asymmetric JWT signing keys
(ES256/RS256, published at ``/auth/v1/.well-known/jwks.json``) or with the legacy
shared HS256 secret. We accept exactly those algorithms, pick the key source by
algorithm (preventing alg-confusion), and require ``aud``, ``exp`` and ``sub``.
Tokens are never logged.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient

from app.config.settings import Settings
from app.core.errors import AuthenticationError
from app.core.logging import get_logger

logger = get_logger("stayops.auth")

ASYMMETRIC_ALGORITHMS = ("ES256", "RS256", "EdDSA")
SYMMETRIC_ALGORITHMS = ("HS256",)


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    user_id: uuid.UUID
    email: str | None
    full_name: str | None
    role: str | None
    claims: dict[str, Any]


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_keys=True, lifespan=600, timeout=5)


def verify_access_token(token: str, settings: Settings) -> VerifiedToken:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid access token.") from exc

    alg = header.get("alg")
    key: Any
    if alg in SYMMETRIC_ALGORITHMS:
        if settings.supabase_jwt_secret is None:
            raise AuthenticationError("Invalid access token.")
        key = settings.supabase_jwt_secret.get_secret_value()
    elif alg in ASYMMETRIC_ALGORITHMS:
        if settings.supabase_jwks_url is None:
            raise AuthenticationError("Invalid access token.")
        try:
            key = _jwks_client(settings.supabase_jwks_url).get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientConnectionError as exc:
            logger.error("auth.jwks_unreachable")
            raise AuthenticationError("Unable to verify the access token right now.") from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Invalid access token.") from exc
    else:
        raise AuthenticationError("Invalid access token.")

    options: dict[str, Any] = {"require": ["exp", "sub", "aud"]}
    decode_kwargs: dict[str, Any] = {
        "algorithms": [alg],
        "audience": settings.supabase_jwt_audience,
        "options": options,
        "leeway": 30,
    }
    if settings.supabase_issuer:
        decode_kwargs["issuer"] = settings.supabase_issuer

    try:
        claims: dict[str, Any] = jwt.decode(token, key, **decode_kwargs)
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("The access token has expired.") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid access token.") from exc

    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except ValueError as exc:
        raise AuthenticationError("Invalid access token.") from exc

    if claims.get("role") not in (None, "authenticated"):
        # e.g. an anon or service-role key presented as a user token.
        raise AuthenticationError("A user access token is required.")

    metadata = claims.get("user_metadata") or {}
    full_name = metadata.get("full_name") or metadata.get("name")
    return VerifiedToken(
        user_id=user_id,
        email=claims.get("email"),
        full_name=full_name if isinstance(full_name, str) else None,
        role=claims.get("role"),
        claims=claims,
    )
