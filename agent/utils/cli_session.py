"""CLI session token issuance and verification.

HS256-signed JWTs identify a CLI user by GitHub login. Tokens are valid for 30
days; if a token is older than 24h, ``should_renew`` returns True so the route
handler can issue a fresh one (sliding window).
"""

from __future__ import annotations

import logging
import os
import time

import jwt

logger = logging.getLogger(__name__)

_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 days
_RENEW_AFTER_SECONDS = 24 * 60 * 60  # 24 hours
_ALGORITHM = "HS256"


def _get_secret() -> str:
    secret = os.environ.get("CLI_SESSION_SECRET", "")
    if not secret:
        raise RuntimeError("CLI_SESSION_SECRET is not configured")
    return secret


def issue_cli_session_token(github_login: str) -> str:
    """Issue a new CLI session JWT for ``github_login``."""
    now = int(time.time())
    payload = {
        "sub": github_login,
        "iat": now,
        "exp": now + _TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, _get_secret(), algorithm=_ALGORITHM)


def verify_cli_session_token(token: str) -> dict | None:
    """Verify ``token``; return claims dict on success, ``None`` on failure."""
    if not token:
        return None
    try:
        claims = jwt.decode(token, _get_secret(), algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
    except Exception:
        logger.exception("Unexpected error verifying CLI session token")
        return None
    if not isinstance(claims, dict) or not claims.get("sub"):
        return None
    return claims


def should_renew(token_claims: dict) -> bool:
    """Return True if the token was issued more than 24h ago."""
    iat = token_claims.get("iat")
    if not isinstance(iat, (int, float)):
        return False
    return (int(time.time()) - int(iat)) > _RENEW_AFTER_SECONDS
