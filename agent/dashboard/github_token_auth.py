"""Who a GitHub user token belongs to, and whether that person is an admin.

The dashboard API is normally cookie-authenticated through the GitHub OAuth
login flow, which automation cannot complete. A CI job (typically in the repo
that builds the sandbox image) instead sends ``Authorization: Bearer <token>``
with a personal access token: the token is resolved to a GitHub identity via
``GET /user``, which must match a ``CONFIGURED_ADMINS`` entry.

Only endpoints that explicitly opt in accept this; the ambient session cookie
remains the only credential for everything else. The browser login flow lands
here too — it resolves the identity behind the token it just exchanged the same
way, so there is one place that knows how to answer "who is this token".
"""

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException, Request

from ..github.api import github_client, github_request, github_url
from ..settings.admin import is_admin

logger = logging.getLogger(__name__)

_GITHUB_TIMEOUT = httpx.Timeout(10.0, connect=3.0)


def bearer_github_token(request: Request) -> str | None:
    """Return the ``Authorization: Bearer`` token, if the request carries one."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.strip().lower() != "bearer":
        return None
    return value.strip() or None


@dataclass(frozen=True)
class GithubIdentity:
    login: str
    email: str | None
    avatar_url: str | None


async def fetch_github_identity(token: str) -> GithubIdentity:
    """Resolve a GitHub token to the account behind it.

    ``GET /user`` only carries an email when the account publishes one, so fall
    back to the primary from ``/user/emails``, otherwise an admin allowlisted by
    email is unauthenticatable. That endpoint needs the token to be able to read
    email addresses; failure just leaves the email unresolved, and login
    matching still works.
    """
    email: str | None = None
    try:
        async with github_client(token=token, timeout=_GITHUB_TIMEOUT) as client:
            response = await github_request(client, "GET", github_url("/user"))
            if response.status_code == 200:
                email = _email_of(response) or _primary_email(
                    await github_request(client, "GET", github_url("/user/emails"))
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
    if not isinstance(data, dict):
        data = {}
    login = data.get("login")
    if not isinstance(login, str) or not login.strip():
        raise HTTPException(401, "GitHub token did not resolve to a user")
    avatar_url = data.get("avatar_url")
    return GithubIdentity(
        login=login.strip(),
        email=email,
        avatar_url=avatar_url if isinstance(avatar_url, str) else None,
    )


def _email_of(response: httpx.Response) -> str | None:
    data = response.json() if response.content else {}
    email = data.get("email") if isinstance(data, dict) else None
    return email.strip() if isinstance(email, str) and email.strip() else None


def _primary_email(response: httpx.Response) -> str | None:
    if response.status_code != 200 or not response.content:
        return None
    entries = response.json()
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("primary"):
            continue
        email = entry.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip()
    return None


async def admin_session_for_github_token(token: str) -> dict[str, Any]:
    """Return a session-shaped identity for an admin's GitHub token.

    Raises 401 when the token is unusable and 403 when its owner is not an admin.
    """
    identity = await fetch_github_identity(token)
    if not is_admin(identity.email, login=identity.login):
        logger.warning("Rejected GitHub-token admin request for non-admin %s", identity.login)
        raise HTTPException(403, "admin only")
    return {"sub": identity.login, "email": identity.email, "auth": "github_token"}
