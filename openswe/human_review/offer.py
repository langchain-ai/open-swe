"""Suggest getting a pull request reviewed to whoever links it in Slack."""

import logging

from openswe.github.http import GitHubAppUnavailable
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequestPayload
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.client import GitHubPrRef
from openswe.slack.suggested_actions import SuggestedAction, register
from openswe.users import User
from openswe.utils.preview import skip_on_preview

logger = logging.getLogger(__name__)

GET_REVIEWED = register(
    SuggestedAction(
        kind="review",
        question="Want Open SWE to get {subject} reviewed?",
        accept_label="Get it reviewed",
        accepted="Asked Open SWE to get {subject} reviewed.",
        request="Get {subject} reviewed.",
        prompt_name="runs/suggested-review",
    )
)


async def _reviewable(pr_ref: GitHubPrRef) -> bool:
    """Whether the pull request is open, not a draft, and could get a review request."""
    if await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number) is not None:
        return False
    try:
        async with PullRequestClient.as_app(pr_ref.owner, pr_ref.repo, pr_ref.number) as pull:
            details = PullRequestPayload.model_validate(await pull.pull())
    except GitHubAppUnavailable:
        return False
    except Exception:
        logger.warning(
            "Could not read a pull request linked in Slack",
            extra={
                "pr_repo_full_name": f"{pr_ref.owner}/{pr_ref.repo}",
                "pr_number": pr_ref.number,
            },
            exc_info=True,
        )
        return False
    return (
        details.state == "open"
        and not details.draft
        and await User.for_login("github", details.author) is not None
    )


async def offer_review(
    channel_id: str, thread_ts: str, reply_thread_ts: str, slack_user_id: str, pr_ref: GitHubPrRef
) -> None:
    """Suggest getting the linked pull request reviewed, when it could be."""
    if skip_on_preview("offer_review"):
        return
    if await User.for_identity("slack", slack_user_id) is None or not await _reviewable(pr_ref):
        return
    await GET_REVIEWED.suggest(
        channel_id,
        slack_user_id,
        pr_ref.url,
        thread_ts=thread_ts,
        reply_thread_ts=reply_thread_ts,
    )
