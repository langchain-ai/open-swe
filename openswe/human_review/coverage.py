"""Which code owners a pull request's changed files need, grouped by owners."""

import logging
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass
from typing import Self

from openswe.expedited_review.eligibility import ChangedFile
from openswe.github.codeowners import CodeOwners
from openswe.github.http import GitHubAppUnavailable
from openswe.github.org_membership import team_members
from openswe.github.pull_request_status import PullRequestClient
from openswe.human_review.requests import HumanReviewRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Area:
    """Changed files that share one set of CODEOWNERS owners."""

    handles: tuple[str, ...]
    owners: frozenset[str]
    files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Coverage:
    """A pull request's owned changed files split by owners, the largest area first.

    The first reviewer comes from the largest area; the rest get one reviewer each after an approval.
    """

    areas: tuple[Area, ...]

    def uncovered(self, logins: Collection[str]) -> list[Area]:
        """Areas none of ``logins`` owns."""
        lowered = {login.lower() for login in logins}
        return [area for area in self.areas if not area.owners & lowered]

    def of(self, login: str) -> list[Area]:
        return [area for area in self.areas if login.lower() in area.owners]

    def owned(self) -> Counter[str]:
        """How many changed files each owner owns."""
        counts: Counter[str] = Counter()
        for area in self.areas:
            for login in area.owners:
                counts[login] += len(area.files)
        return counts

    @classmethod
    async def build(cls, codeowners: CodeOwners, paths: list[str]) -> Self:
        grouped: dict[tuple[str, ...], list[str]] = {}
        for path in paths:
            if handles := codeowners.owners_for(path):
                grouped.setdefault(handles, []).append(path)
        teams: dict[str, list[str]] = {}
        areas: list[Area] = []
        for handles, files in grouped.items():
            owners: set[str] = set()
            for owner in handles:
                handle = owner.removeprefix("@")
                if "/" not in handle:
                    owners.add(handle.lower())
                    continue
                if handle not in teams:
                    org, slug = handle.split("/", 1)
                    teams[handle] = await team_members(org, slug) or []
                    logger.info(
                        "Expanded a CODEOWNERS team",
                        extra={"github_team": handle, "members": len(teams[handle])},
                    )
                owners.update(login.lower() for login in teams[handle])
            areas.append(Area(handles, frozenset(owners), tuple(files)))
        return cls(tuple(sorted(areas, key=lambda area: (-len(area.files), area.handles))))

    @classmethod
    async def load(cls, request: HumanReviewRequest) -> Self | None:
        """The request's coverage; ``None`` without CODEOWNERS or when GitHub cannot be read."""
        pr = request.pull_request
        try:
            async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
                files = await ChangedFile.of_pull(pull)
                if not files:
                    return None
                codeowners = await CodeOwners.fetch(pull.repo, pr.base_ref or None)
        except GitHubAppUnavailable:
            logger.warning(
                "No GitHub App token to read code-owner coverage",
                extra={"request_id": str(request.id)},
            )
            return None
        if codeowners is None:
            return None
        return await cls.build(codeowners, [changed.filename for changed in files])
