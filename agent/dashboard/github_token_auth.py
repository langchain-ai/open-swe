"""Admin authentication for non-browser callers using a GitHub user token.

The dashboard API is normally cookie-authenticated through the GitHub OAuth
login flow, which automation cannot complete. A CI job (typically in the repo
that builds the sandbox image) instead sends ``Authorization: Bearer <token>``
with a personal access token: the token is resolved to a GitHub identity via
``GET /user``, which must match a ``CONFIGURED_ADMINS`` entry.

Only endpoints that explicitly opt in accept this; the ambient session cookie
remains the only credential for everything else.
"""

import logging
from typing import Any

import httpx
from fastapi import HTTPException, Request

from .admin import is_admin

logger = logging.getLogger(__name__)

_GITHUB_USER_URL = "https://api.github.com/user"
_GITHUB_TIMEOUT = httpx.Timeout(10.0, connect=3.0)


def bearer_github_token(request: Request) -> str | None:
    """Return the ``Authorization: Bearer`` token, if the request carries one."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.strip().lower() != "bearer":
        return None
    return value.strip() or None


async def _github_identity(token: str) -> tuple[str, str | None]:
    """Resolve a GitHub token to ``(login, email)``."""
    try:
        async with httpx.AsyncClient(timeout=_GITHUB_TIMEOUT) as client:
            response = await client.get(
                _GITHUB_USER_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
    except httpx.HTTPError as e:
        raise HTTPException(502, f"could not verify GitHub token: {e}") from e

    if response.status_code == 403:
        raise HTTPException(
            401,
            "GitHub token cannot identify a user — use a personal access token "
            "for an admin account, not a GitHub App installation token",
        )
    if response.status_code >= 400:
        raise HTTPException(401, "invalid GitHub token")

    data = response.json() if response.content else {}
    login = data.get("login") if isinstance(data, dict) else None
    if not isinstance(login, str) or not login.strip():
        raise HTTPException(401, "GitHub token did not resolve to a user")
    email = data.get("email") if isinstance(data, dict) else None
    return login.strip(), email if isinstance(email, str) and email.strip() else None


async def admin_session_for_github_token(token: str) -> dict[str, Any]:
    """Return a session-shaped identity for an admin's GitHub token.

    Raises 401 when the token is unusable and 403 when its owner is not an admin.
    """
    login, email = await _github_identity(token)
    if not is_admin(email, login=login):
        logger.warning("Rejected GitHub-token admin request for non-admin %s", login)
        raise HTTPException(403, "admin only")
    return {"sub": login, "email": email, "auth": "github_token"}
