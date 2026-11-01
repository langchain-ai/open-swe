"""Who an unclaimed review request's deadline may pick, worked out once per head SHA."""

import asyncio
import logging
from uuid import UUID

from openswe.github.http import GitHubAppUnavailable, GitHubClient
from openswe.human_review.picking import Candidates
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import ReviewChannelUnknownError, in_review_channel
from openswe.users import User

logger = logging.getLogger(__name__)

_KEY = "tentative_reviewers"


class TentativeReviewers:
    """The people Open SWE may pick for ``request``, by ``users.id``, cached in its run config."""

    def __init__(self, request: HumanReviewRequest) -> None:
        self.request = request

    @property
    def _cached(self) -> frozenset[UUID] | None:
        cached = self.request.run_config.get(_KEY)
        if not isinstance(cached, dict) or cached.get("head_sha") != self.request.head_sha:
            return None
        user_ids = cached.get("user_ids")
        if not isinstance(user_ids, list):
            return None
        return frozenset(UUID(user_id) for user_id in user_ids if isinstance(user_id, str))

    async def user_ids(self) -> frozenset[UUID]:
        if (cached := self._cached) is not None:
            return cached
        logins = await self._gather()
        if logins is None:
            return frozenset()
        users = await asyncio.gather(*(User.for_login("github", login) for login in logins))
        user_ids = frozenset(user.id for user in users if user is not None)
        async with HumanReviewRequest.locked(self.request.id) as (_, row):
            if row is not None:
                row.run_config = {
                    **row.run_config,
                    _KEY: {
                        "head_sha": self.request.head_sha,
                        "user_ids": sorted(str(user_id) for user_id in user_ids),
                    },
                }
        return user_ids

    async def _gather(self) -> frozenset[str] | None:
        """The GitHub logins candidates are known by; ``None`` when GitHub cannot say."""
        auto_assigned = await self._auto_assigned()
        if auto_assigned is None:
            return None
        if not auto_assigned:
            return frozenset()
        candidates = await Candidates.of_request(self.request)
        return candidates.tentative(self.request) if candidates is not None else None

    async def _auto_assigned(self) -> bool | None:
        """Whether the deadline picks at all: a post outside a review channel only gets reactions."""
        request = self.request
        if request.kind != "posted" or request.auto_assign_asked:
            return True
        pr = request.pull_request
        try:
            async with GitHubClient.as_app(pr.owner, pr.repo) as github:
                return await in_review_channel(
                    github.repo(pr.owner, pr.repo), request.slack_channel_id
                )
        except GitHubAppUnavailable, ReviewChannelUnknownError:
            logger.warning(
                "Could not tell whether a posted pull request will be auto-assigned",
                extra={"request_id": str(request.id)},
                exc_info=True,
            )
            return None
