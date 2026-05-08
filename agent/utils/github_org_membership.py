"""GitHub organization membership checks for webhook gating."""

from __future__ import annotations

import logging

import httpx

from .github_app import get_github_app_installation_token

logger = logging.getLogger(__name__)

INTERNAL_BOT_LOGINS: frozenset[str] = frozenset({"open-swe[bot]", "openswe-dev[bot]"})


async def is_user_active_org_member(username: str, org: str) -> bool:
    """Return True if ``username`` is an *active* member of ``org``.

    Uses the GitHub App installation token so that private organization
    memberships are visible (the same approach as the reference
    ``tag-external-contributions.yml`` workflow). On any API error, returns
    ``False`` — fail-closed for security.
    """
    if not username or not org:
        return False

    token = await get_github_app_installation_token()
    if not token:
        logger.warning(
            "GitHub App token unavailable; cannot verify org membership for %s", username
        )
        return False

    url = f"https://api.github.com/orgs/{org}/memberships/{username}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except Exception:
        logger.exception("Error calling GitHub org membership API for %s/%s", org, username)
        return False

    if response.status_code == 404:
        return False
    if response.status_code != 200:
        logger.warning(
            "Unexpected status %s checking %s membership for %s",
            response.status_code,
            org,
            username,
        )
        return False

    try:
        state = response.json().get("state")
    except ValueError:
        logger.warning("Failed to parse org membership response for %s/%s", org, username)
        return False
    return state == "active"
