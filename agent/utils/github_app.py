"""GitHub App installation token generation."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Sequence
from typing import Any

import httpx
import jwt

logger = logging.getLogger(__name__)

GITHUB_APP_ID = os.environ.get("GITHUB_APP_ID", "")
GITHUB_APP_PRIVATE_KEY = os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
GITHUB_APP_INSTALLATION_ID = os.environ.get("GITHUB_APP_INSTALLATION_ID", "")


def _generate_app_jwt() -> str:
    """Generate a short-lived JWT signed with the GitHub App private key."""
    now = int(time.time())
    payload = {
        "iat": now - 60,  # issued 60s ago to account for clock skew
        "exp": now + 540,  # expires in 9 minutes (max is 10)
        "iss": GITHUB_APP_ID,
    }
    private_key = GITHUB_APP_PRIVATE_KEY.replace("\\n", "\n")
    return jwt.encode(payload, private_key, algorithm="RS256")


async def get_github_app_installation_token(
    *,
    repository_ids: Sequence[int] | None = None,
    repositories: Sequence[str] | None = None,
) -> str | None:
    """Exchange the GitHub App JWT for an installation access token."""
    token, _ = await get_github_app_installation_token_with_expiry(
        repository_ids=repository_ids,
        repositories=repositories,
    )
    return token


async def get_github_app_installation_token_with_expiry(
    *,
    repository_ids: Sequence[int] | None = None,
    repositories: Sequence[str] | None = None,
) -> tuple[str | None, str | None]:
    """Exchange the GitHub App JWT for an installation access token and its expiry."""
    if not GITHUB_APP_ID or not GITHUB_APP_PRIVATE_KEY or not GITHUB_APP_INSTALLATION_ID:
        logger.debug("GitHub App env vars not fully configured, skipping app token")
        return None, None

    body: dict[str, Any] = {}
    if repository_ids:
        body["repository_ids"] = list(repository_ids)
    elif repositories:
        body["repositories"] = list(repositories)

    try:
        app_jwt = _generate_app_jwt()
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.github.com/app/installations/{GITHUB_APP_INSTALLATION_ID}/access_tokens",
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=body or None,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("token"), data.get("expires_at")
    except Exception:
        logger.exception("Failed to get GitHub App installation token")
        return None, None
