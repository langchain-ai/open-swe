"""GitHub organization membership checks for webhook gating."""

import logging
from urllib.parse import quote

import httpx2
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from agent.config import ENV
from agent.github.app import (
    get_github_app_installation_id_for_org,
    get_github_app_installation_token,
)
from agent.github.http import GITHUB_API_BASE, github_client, github_request

logger = logging.getLogger(__name__)

# The GitHub Apps Open SWE acts as; events they send are Open SWE's own.
OPEN_SWE_GITHUB_LOGINS: frozenset[str] = frozenset({"open-swe[bot]", "openswe-dev[bot]"})
INTERNAL_BOT_LOGINS: frozenset[str] = frozenset(
    OPEN_SWE_GITHUB_LOGINS
    | {login.strip() for login in ENV.EXTRA_INTERNAL_BOT_LOGINS.get().split(",") if login.strip()}
)


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
        async with httpx2.AsyncClient(timeout=10.0) as client:
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


class _TeamMember(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str
    type: str = "User"


_TEAM_MEMBERS = TypeAdapter(list[_TeamMember])
_TEAM_PAGE_SIZE = 100
_TEAM_MAX_PAGES = 10


async def team_members(org: str, team_slug: str) -> list[str] | None:
    """Logins of the people in ``org/team_slug``, child teams included; ``None`` if unreadable."""
    installation_id = await get_github_app_installation_id_for_org(org)
    token = (
        await get_github_app_installation_token(
            installation_id=installation_id, permissions={"members": "read"}
        )
        if installation_id
        else None
    )
    extra = {"github_org": org, "github_team": team_slug}
    if not token:
        logger.warning("No GitHub App token to read team members", extra=extra)
        return None
    url = f"{GITHUB_API_BASE}/orgs/{quote(org, safe='')}/teams/{quote(team_slug, safe='')}/members"
    logins: list[str] = []
    try:
        async with github_client(token=token) as client:
            for page in range(1, _TEAM_MAX_PAGES + 1):
                response = await github_request(
                    client, "GET", url, params={"per_page": _TEAM_PAGE_SIZE, "page": page}
                )
                response.raise_for_status()
                members = _TEAM_MEMBERS.validate_json(response.content)
                logins.extend(member.login for member in members if member.type == "User")
                if len(members) < _TEAM_PAGE_SIZE:
                    break
    except httpx2.HTTPError, ValidationError:
        logger.warning("Could not read team members", extra=extra, exc_info=True)
        return None
    return logins
