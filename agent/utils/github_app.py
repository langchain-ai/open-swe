"""GitHub App installation token generation."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

import httpx
import jwt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitHubAppConfig:
    app_id: str
    private_key: str
    installation_id: str


def _get_github_app_config() -> GitHubAppConfig:
    """Read GitHub App config from the current process environment."""
    return GitHubAppConfig(
        app_id=os.environ.get("GITHUB_APP_ID", ""),
        private_key=os.environ.get("GITHUB_APP_PRIVATE_KEY", ""),
        installation_id=os.environ.get("GITHUB_APP_INSTALLATION_ID", ""),
    )


def _generate_app_jwt(config: GitHubAppConfig) -> str:
    """Generate a short-lived JWT signed with the GitHub App private key."""
    now = int(time.time())
    payload = {
        "iat": now - 60,  # issued 60s ago to account for clock skew
        "exp": now + 540,  # expires in 9 minutes (max is 10)
        "iss": config.app_id,
    }
    private_key = config.private_key.replace("\\n", "\n")
    return jwt.encode(payload, private_key, algorithm="RS256")


async def get_github_app_installation_token() -> str | None:
    """Exchange the GitHub App JWT for an installation access token.

    Returns:
        Installation access token string, or None if unavailable.
    """
    config = _get_github_app_config()
    if not config.app_id or not config.private_key or not config.installation_id:
        logger.debug("GitHub App env vars not fully configured, skipping app token")
        return None

    try:
        app_jwt = _generate_app_jwt(config)
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.github.com/app/installations/{config.installation_id}/access_tokens",
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
