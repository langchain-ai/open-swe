"""Who and what this deployment may act on: GitHub orgs, repos, and its own bots."""

import logging
from urllib.parse import quote

import httpx

from ..config import (
    allowed_github_orgs,
    allowed_github_repos,
    extra_internal_bot_logins,
)
from .github_app import (
    get_github_app_installation_id_for_org,
    get_github_app_installation_token,
)

logger = logging.getLogger(__name__)

_BUILT_IN_BOT_LOGINS = frozenset({"open-swe[bot]", "openswe-dev[bot]"})


def internal_bot_logins() -> frozenset[str]:
    """Bot accounts that are this deployment talking to itself."""
    return _BUILT_IN_BOT_LOGINS | extra_internal_bot_logins()


def is_repo_allowed(repo_config: dict[str, str]) -> bool:
    """Whether the agent may act on a repository.

    True when no allow-list is configured at all, when the owner is an allowed
    org, or when ``owner/name`` is explicitly allowed.
    """
    orgs = allowed_github_orgs()
    repos = allowed_github_repos()
    if not orgs and not repos:
        return True
    owner = repo_config.get("owner", "").lower()
    name = repo_config.get("name", "").lower()
    return owner in orgs or f"{owner}/{name}" in repos


async def is_login_in_allowed_org(login: str) -> bool:
    """Whether ``login`` is an active member of any configured allowed org.

    False when no orgs are configured; callers that treat "no allow-list" as
    fail-open check :func:`agent.config.allowed_github_orgs` themselves.
    """
    if not login:
        return False
    for org in sorted(allowed_github_orgs()):
        if await is_user_active_org_member(login, org):
            return True
    return False


async def is_user_active_org_member(username: str, org: str) -> bool:
    """Return True if ``username`` is an *active* member of ``org``.

    Uses the GitHub App installation token so that private organization
    memberships are visible (the same approach as the reference
    ``tag-external-contributions.yml`` workflow). On any API error, returns
    ``False`` — fail-closed for security.

    Requires the GitHub App to have the ``Organization -> Members: Read-only``
    permission; the ``GET /orgs/{org}/memberships/{username}`` endpoint returns
    403 (-> ``False``) without it. See docs/INSTALLATION.md.
    """
    if not username or not org:
        return False

    installation_id = await get_github_app_installation_id_for_org(org)
    token = (
        await get_github_app_installation_token(
            installation_id=installation_id,
            permissions={"members": "read"},
        )
        if installation_id
        else None
    )
    if not token:
        logger.warning(
            "GitHub App token unavailable; cannot verify org membership for %s", username
        )
        return False

    url = (
        f"https://api.github.com/orgs/{quote(org, safe='')}/memberships/{quote(username, safe='')}"
    )
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
