"""GitHub App installation token generation."""

from __future__ import annotations

import logging
import os
import time

import httpx
import jwt

from agent.utils.http import get_http_client

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


async def get_github_app_installation_token() -> str | None:
    """Exchange the GitHub App JWT for an installation access token.

    Returns:
        Installation access token string, or None if unavailable.
    """
    if not GITHUB_APP_ID or not GITHUB_APP_PRIVATE_KEY or not GITHUB_APP_INSTALLATION_ID:
        logger.debug("GitHub App env vars not fully configured, skipping app token")
        return None

    try:
        app_jwt = _generate_app_jwt()
        client = get_http_client()
        response = await client.post(
            f"https://api.github.com/app/installations/{GITHUB_APP_INSTALLATION_ID}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        response.raise_for_status()
        return response.json().get("token")
    except Exception:
        logger.exception("Failed to get GitHub App installation token")
        return None
