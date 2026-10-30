"""Choose the review workflow for a dashboard request."""

from dataclasses import dataclass
from uuid import UUID

from openswe.dashboard.workspace_settings import get_workspace_settings
from openswe.expedited_review.nomination import ExpeditedReviewIneligible, ExpeditedReviewNomination
from openswe.github.http import GitHubAppUnavailable
from openswe.github.pull_request_status import PullRequestClient
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import (
    Origin,
    RequestResult,
    request_review,
    review_blockers,
    target_channel,
)
from openswe.slack.client import GitHubPrRef, get_slack_permalink


@dataclass(frozen=True, slots=True)
class DashboardReviewRequest:
    pr_ref: GitHubPrRef
    origin: Origin
    token: str

    async def request(
        self, *, channel: str = "", inline_summary: str | None = None
    ) -> RequestResult:
        pr = self.pr_ref
        active = await HumanReviewRequest.active_for(pr.owner, pr.repo, pr.number)
        if active is None and (await get_workspace_settings()).expedited_review_enabled:
            try:
                async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
                    blockers = await review_blockers(pull)
                    if blockers == []:
                        target = await target_channel(pr, pull, channel)
                        if isinstance(target, RequestResult):
                            return target
                        try:
                            result = await ExpeditedReviewNomination(
                                pr, self.origin, self.token
                            ).start(
                                target.id,
                                inline_summary=inline_summary or "",
                                replace_standard=False,
                            )
                        except ExpeditedReviewIneligible:
                            pass
                        else:
                            if not result["success"]:
                                return RequestResult(success=False, error=str(result["error"]))
                            approval = await HumanReviewRequest.get(UUID(result["approval_id"]))
                            return RequestResult(
                                success=True,
                                request_id=str(result["approval_id"]),
                                channel=target.id,
                                channel_name=target.name,
                                permalink=(
                                    await get_slack_permalink(
                                        approval.slack_channel_id, approval.slack_message_ts
                                    )
                                    or ""
                                    if approval is not None
                                    else ""
                                ),
                            )
            except GitHubAppUnavailable:
                return RequestResult(
                    success=False,
                    error="Open SWE cannot reach this repository's GitHub App installation.",
                )
        return await request_review(pr, self.origin, channel=channel, inline_summary=inline_summary)
