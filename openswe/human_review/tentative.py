"""Who an unclaimed review request's deadline may pick, worked out once per head SHA."""

import logging

from openswe.github.http import GitHubAppUnavailable, GitHubClient
from openswe.human_review.picking import Candidates
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import ReviewChannelUnknownError, in_review_channel

logger = logging.getLogger(__name__)

_KEY = "tentative_reviewers"


class TentativeReviewers:
    """The lowercase logins Open SWE may pick for ``request``, cached in its run config."""

    def __init__(self, request: HumanReviewRequest) -> None:
        self.request = request

    @property
    def _cached(self) -> frozenset[str] | None:
        cached = self.request.run_config.get(_KEY)
        if not isinstance(cached, dict) or cached.get("head_sha") != self.request.head_sha:
            return None
        logins = cached.get("logins")
        if not isinstance(logins, list):
            return None
        return frozenset(login for login in logins if isinstance(login, str))

    async def logins(self) -> frozenset[str]:
        if (cached := self._cached) is not None:
            return cached
        logins = await self._gather()
        if logins is None:
            return frozenset()
        async with HumanReviewRequest.locked(self.request.id) as (_, row):
            if row is not None:
                row.run_config = {
                    **row.run_config,
                    _KEY: {"head_sha": self.request.head_sha, "logins": sorted(logins)},
                }
        return logins

    async def _gather(self) -> frozenset[str] | None:
        """``None`` when GitHub cannot say, so the next read tries again."""
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
