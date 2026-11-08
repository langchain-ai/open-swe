"""Choose the review workflow for a dashboard request."""

import logging
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
from openswe.slack.client import GitHubPrRef

logger = logging.getLogger(__name__)


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
        if active is not None and active.kind == "expedited":
            if active.slack_message_ts and not self.origin.asked(active):
                return RequestResult(
                    success=True,
                    reused=True,
                    request_id=str(active.id),
                    channel=active.slack_channel_id,
                    permalink=await active.card_url(),
                    auto_merge=active.auto_merge,
                )
            try:
                result = await ExpeditedReviewNomination(pr, self.origin, self.token).start(
                    active.slack_channel_id,
                    active.slack_thread_ts,
                    inline_summary=inline_summary,
                    replace_standard=False,
                )
            except ExpeditedReviewIneligible as exc:
                return RequestResult(success=False, error=str(exc))
            if not result["success"]:
                return RequestResult(success=False, error=str(result["error"]))
            approval = await HumanReviewRequest.get(UUID(str(result["approval_id"])))
            if approval is None or not approval.slack_message_ts:
                return RequestResult(
                    success=False,
                    error="The review card is not posted yet. Check the author's readiness prompt in Slack or retry once the pull request is ready.",
                )
            return RequestResult(
                success=True,
                reused=approval.id == active.id,
                request_id=str(approval.id),
                channel=approval.slack_channel_id,
                permalink=await approval.card_url(),
                auto_merge=approval.auto_merge,
            )
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
                                inline_summary=inline_summary,
                                replace_standard=False,
                            )
                        except ExpeditedReviewIneligible as exc:
                            logger.info(
                                "Dashboard review falling back to standard review",
                                extra={"pr_url": pr.url, "reason": str(exc)},
                            )
                        else:
                            if not result["success"]:
                                logger.warning(
                                    "Expedited dashboard review failed; requesting standard review",
                                    extra={"pr_url": pr.url, "reason": str(result["error"])},
                                )
                                return await request_review(
                                    pr, self.origin, channel=channel, inline_summary=inline_summary
                                )
                            approval = await HumanReviewRequest.get(UUID(result["approval_id"]))
                            return RequestResult(
                                success=True,
                                request_id=str(result["approval_id"]),
                                channel=target.id,
                                channel_name=target.name,
                                permalink=(
                                    await approval.card_url() if approval is not None else ""
                                ),
                            )
            except GitHubAppUnavailable:
                return RequestResult(
                    success=False,
                    error="Robin cannot reach this repository's GitHub App installation.",
                )
        return await request_review(pr, self.origin, channel=channel, inline_summary=inline_summary)
