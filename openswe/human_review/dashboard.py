"""Choose the review workflow for a dashboard request."""

import logging
from dataclasses import dataclass
from uuid import UUID

from openswe.dashboard.settings import get_settings
from openswe.expedited_review.nomination import ExpeditedReviewIneligible, ExpeditedReviewNomination
from openswe.github.http import GitHubAppUnavailable
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequestPayload
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import (
    Origin,
    RequestResult,
    request_review,
    review_blockers,
    target_channel,
)
from openswe.slack.client import GitHubPrRef
from openswe.utils.startup_trace import apm_span

logger = logging.getLogger(__name__)


_APP_UNAVAILABLE = "Robin cannot reach this repository's GitHub App installation."


@dataclass(frozen=True, slots=True)
class DashboardReviewRequest:
    pr_ref: GitHubPrRef
    origin: Origin
    token: str

    async def request(
        self, *, channel: str = "", inline_summary: str | None = None
    ) -> RequestResult:
        pr = self.pr_ref
        span_tags = {"pull_request.url": pr.url}
        active = await HumanReviewRequest.active_for(pr.owner, pr.repo, pr.number)
        if active is not None and active.kind == "expedited":
            if (active.slack_message_ts or active.awaiting_ready) and not self.origin.asked(active):
                return RequestResult(
                    success=True,
                    reused=True,
                    request_id=str(active.id),
                    channel=active.slack_channel_id,
                    permalink=await active.permalink(self.origin.requester),
                    auto_merge=active.auto_merge,
                )
            try:
                result = await ExpeditedReviewNomination(
                    pr, self.origin, self.token, read_as_app=True
                ).start(
                    active.slack_channel_id,
                    active.slack_thread_ts,
                    inline_summary=inline_summary,
                    replace_standard=False,
                )
            except ExpeditedReviewIneligible as exc:
                return RequestResult(success=False, error=str(exc))
            except GitHubAppUnavailable:
                return RequestResult(success=False, error=_APP_UNAVAILABLE)
            if not result["success"]:
                return RequestResult(success=False, error=str(result["error"]))
            approval = await HumanReviewRequest.get(UUID(str(result["approval_id"])))
            if approval is None or (not approval.slack_message_ts and not approval.awaiting_ready):
                return RequestResult(
                    success=False,
                    error="The review card is not posted yet. Check the author's confirmation prompt in Slack or retry after they confirm the request.",
                )
            return RequestResult(
                success=True,
                reused=approval.id == active.id,
                request_id=str(approval.id),
                channel=approval.slack_channel_id,
                permalink=await approval.permalink(self.origin.requester),
                auto_merge=approval.auto_merge,
            )
        if active is None and (await get_settings()).expedited_review_enabled:
            try:
                async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
                    with apm_span("human_review.read_pull_request", span_tags):
                        snapshot = await PullRequestPayload.read(pull)
                    with apm_span("human_review.blockers", span_tags):
                        blockers = (
                            None if snapshot is None else await review_blockers(pull, snapshot)
                        )
                    if blockers == []:
                        with apm_span("human_review.target_channel", span_tags):
                            target = await target_channel(pr, pull, channel, snapshot)
                        if isinstance(target, RequestResult):
                            return target
                        with apm_span("human_review.expedited", span_tags):
                            try:
                                result = await ExpeditedReviewNomination(
                                    pr, self.origin, self.token, read_as_app=True
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
                                result = None
                        if result is not None:
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
                                    await approval.permalink(self.origin.requester)
                                    if approval
                                    else ""
                                ),
                            )
            except GitHubAppUnavailable:
                return RequestResult(success=False, error=_APP_UNAVAILABLE)
        with apm_span("human_review.standard", span_tags):
            return await request_review(
                pr, self.origin, channel=channel, inline_summary=inline_summary
            )
