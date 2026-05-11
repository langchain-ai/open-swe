"""FastAPI dependency that authenticates CLI requests.

Verifies the bearer-token JWT issued by :mod:`agent.utils.cli_session` and
re-checks GitHub org membership (cached for 60s). Attaches a :class:`CliUser`
to ``request.state.cli_user``.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from ..utils.cli_session import verify_cli_session_token
from ..utils.github_org_membership import is_user_active_org_member

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60


@dataclass(frozen=True)
class CliUser:
    """Authenticated CLI requester."""

    github_login: str
    token_claims: dict


# {github_login: (verified_at_epoch, is_member)}
_ORG_MEMBERSHIP_CACHE: dict[str, tuple[float, bool]] = {}


def _allowed_org() -> str:
    return os.environ.get("ALLOWED_GITHUB_ORG", "").strip()


def _extract_bearer_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    return auth[7:].strip() or None


async def _check_org_membership(github_login: str, org: str) -> bool:
    """Resolve org membership with 60s in-process cache and outage tolerance."""
    now = time.time()
    cached = _ORG_MEMBERSHIP_CACHE.get(github_login)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        is_member = await is_user_active_org_member(github_login, org)
    except Exception:
        logger.exception("Org membership check raised for %s/%s", org, github_login)
        if cached is not None:
            if cached[1] is False:
                return False
            return True
        raise HTTPException(status_code=503, detail="GitHub unavailable") from None

    _ORG_MEMBERSHIP_CACHE[github_login] = (now, is_member)
    return is_member


async def require_cli_user(request: Request) -> CliUser:
    """FastAPI dependency: validate JWT and org membership; return ``CliUser``."""
    token = _extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    claims = verify_cli_session_token(token)
    if not claims:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    github_login = str(claims.get("sub") or "")
    if not github_login:
        raise HTTPException(status_code=401, detail="Token missing subject")

    org = _allowed_org()
    if not org:
        logger.error("ALLOWED_GITHUB_ORG is not configured; refusing CLI request")
        raise HTTPException(status_code=503, detail="CLI auth not configured")

    is_member = await _check_org_membership(github_login, org)
    if not is_member:
        raise HTTPException(status_code=403, detail="Not an org member")

    user = CliUser(github_login=github_login, token_claims=claims)
    request.state.cli_user = user
    return user
