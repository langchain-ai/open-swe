"""GitHub organization membership checks for webhook gating."""

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

import httpx2

from openswe.config import ENV
from openswe.github.http import GitHubAppUnavailable, GitHubClient

logger = logging.getLogger(__name__)

# The GitHub Apps Open SWE acts as; events they send are Open SWE's own.
OPEN_SWE_GITHUB_LOGINS: frozenset[str] = frozenset(
    {"open-swe[bot]", "open-swe-preview[bot]", "openswe-dev[bot]", "robin-agent[bot]"}
)
# GraphQL names a GitHub App author by its slug, without the ``[bot]`` REST adds.
OPEN_SWE_AUTHOR_LOGINS: frozenset[str] = OPEN_SWE_GITHUB_LOGINS | {
    login.removesuffix("[bot]") for login in OPEN_SWE_GITHUB_LOGINS
}
INTERNAL_BOT_LOGINS: frozenset[str] = frozenset(
    OPEN_SWE_GITHUB_LOGINS
    | {
        login.strip().lower()
        for login in ENV.EXTRA_INTERNAL_BOT_LOGINS.get().split(",")
        if login.strip()
    }
)


@dataclass(frozen=True, slots=True)
class Org:
    """A GitHub organization's people, read as the App with ``members: read``.

    The installation token sees private memberships, as the reference
    ``tag-external-contributions.yml`` workflow does. It needs the App's
    ``Organization -> Members: Read-only`` permission; see docs/INSTALLATION.md.
    Team members are read fresh every time: a CODEOWNERS approval must stop counting
    as soon as its author leaves the team, and repeated reads cost no quota once
    GitHub answers ``304``.
    """

    name: str

    MEMBERS_READ: ClassVar[Mapping[str, str]] = {"members": "read"}
    MEMBERSHIP_TIMEOUT: ClassVar[float] = 5.0

    async def has_active_member(self, username: str) -> bool:
        """Whether ``username`` is an *active* member; any failure answers ``False``."""
        if not username or not self.name:
            return False
        extra = {"github_org": self.name, "github_login": username}
        try:
            async with (
                asyncio.timeout(self.MEMBERSHIP_TIMEOUT),
                GitHubClient.as_app(
                    self.name, permissions=self.MEMBERS_READ, timeout=self.MEMBERSHIP_TIMEOUT
                ) as github,
            ):
                state = await github.org_membership_state(self.name, username)
        except GitHubAppUnavailable:
            logger.warning("No GitHub App token to verify org membership", extra=extra)
            return False
        except Exception:
            logger.exception("Could not verify org membership", extra=extra)
            return False
        return state == "active"

    async def team_members(self, team_slug: str) -> list[str] | None:
        """Logins in ``team_slug``, child teams included; ``None`` if the team can't be read."""
        try:
            async with GitHubClient.as_app(self.name, permissions=self.MEMBERS_READ) as github:
                return await github.team_members(self.name, team_slug)
        except GitHubAppUnavailable, httpx2.HTTPError, ValueError:
            logger.warning(
                "Could not read team members",
                extra={"github_org": self.name, "github_team": team_slug},
                exc_info=True,
            )
            return None


async def is_user_active_org_member(username: str, org: str) -> bool:
    """Return True if ``username`` is an *active* member of ``org``; fail-closed."""
    return await Org(org).has_active_member(username)


async def team_members(org: str, team_slug: str) -> list[str] | None:
    """Logins of the people in ``org/team_slug``, child teams included; ``None`` if unreadable."""
    return await Org(org).team_members(team_slug)
