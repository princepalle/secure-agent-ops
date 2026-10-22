"""JWT access tokens (HS256).

Tokens carry the user id and tenant id. Role is included for client display
only: the API reloads the user on every request and authorises from the
database record, so a role change or deactivation takes effect immediately.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.config import Settings

ALGORITHM = "HS256"


class TokenError(Exception):
    pass


def create_access_token(*, user_id: str, tenant_id: str, role: str, settings: Settings) -> tuple[str, int]:
    now = datetime.now(UTC)
    expires_in = settings.access_token_minutes * 60
    claims = {
        "sub": user_id,
        "tid": tenant_id,
        "role": role,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(seconds=expires_in),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, settings.jwt_secret_value, algorithm=ALGORITHM), expires_in


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret_value,
            algorithms=[ALGORITHM],  # pinned: rejects "none" and algorithm-confusion attacks
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "tid", "exp", "iat", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("invalid or expired token") from exc
