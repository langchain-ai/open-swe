"""Suggest getting a pull request reviewed to whoever links it in Slack."""

import logging

from openswe.github.http import GitHubAppUnavailable
from openswe.github.org_membership import OPEN_SWE_GITHUB_LOGINS
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest, PullRequestPayload
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.client import GitHubPrRef, parse_github_pr_url
from openswe.slack.suggested_actions import SuggestedAction, register
from openswe.users import User
from openswe.utils.preview import skip_on_preview

logger = logging.getLogger(__name__)


async def _unavailable_reason(subject: str) -> str | None:
    """Explain why an offered review is no longer useful, using current PR state."""
    pr_ref = parse_github_pr_url(subject)
    if pr_ref is None:
        return "This review suggestion is no longer available."
    try:
        if (
            await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
            is not None
        ):
            return f"A review has already been requested for {subject}."
        async with PullRequestClient.as_app(pr_ref.owner, pr_ref.repo, pr_ref.number) as pull:
            details = PullRequestPayload.model_validate(await pull.pull())
            if details.merged or details.state != "open":
                return f"{subject} has already been merged or closed."
            if details.draft:
                return f"{subject} is now a draft. Its author must mark it ready before review."
            if await User.for_login("github", details.author) is None:
                return "This review suggestion is no longer available."
            stored = await PullRequest.get(pr_ref.owner, pr_ref.repo, pr_ref.number)
            if stored and any(review.head_sha == details.head_sha for review in stored.reviews):
                return f"Open SWE has already reviewed the current changes in {subject}."
            for review in await pull.reviews():
                user = review.get("user")
                if (
                    isinstance(user, dict)
                    and user.get("login") in OPEN_SWE_GITHUB_LOGINS
                    and review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}
                    and review.get("commit_id") == details.head_sha
                ):
                    return f"Open SWE has already reviewed the current changes in {subject}."
    except GitHubAppUnavailable:
        logger.info("GitHub access unavailable for a review suggestion", exc_info=True)
        return "This review suggestion is no longer available: GitHub access is unavailable."
    except Exception:
        logger.warning(
            "Could not read a pull request linked in Slack",
            extra={
                "pr_repo_full_name": f"{pr_ref.owner}/{pr_ref.repo}",
                "pr_number": pr_ref.number,
            },
            exc_info=True,
        )
        return "Could not check the current PR status. Please try requesting a review again later."
    return None


GET_REVIEWED = register(
    SuggestedAction(
        kind="review",
        question="Want Open SWE to get {subject} reviewed?",
        accept_label="Get it reviewed",
        accepted="Asked Open SWE to get {subject} reviewed.",
        request="Get {subject} reviewed.",
        prompt_name="runs/suggested-review",
        unavailable_reason=_unavailable_reason,
    )
)


async def offer_review(
    channel_id: str, thread_ts: str, reply_thread_ts: str, slack_user_id: str, pr_ref: GitHubPrRef
) -> None:
    """Suggest getting the linked pull request reviewed, when it could be."""
    if skip_on_preview("offer_review"):
        return
    if (
        await User.for_identity("slack", slack_user_id) is None
        or await _unavailable_reason(pr_ref.url) is not None
    ):
        return
    await GET_REVIEWED.suggest(
        channel_id,
        slack_user_id,
        pr_ref.url,
        thread_ts=thread_ts,
        reply_thread_ts=reply_thread_ts,
    )
