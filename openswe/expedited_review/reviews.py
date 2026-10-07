"""The GitHub review behind each expedited approval, from the click until the card closes."""

import logging

import httpx2

from openswe.github.http import GitHubClient, GitHubError, GitHubSignInRequired, RepoClient
from openswe.human_review.requests import HumanReviewParticipant, HumanReviewRequest
from openswe.slack.client import get_slack_permalink
from openswe.slack.code_channels import is_code_channel_session
from openswe.utils.dashboard_links import dashboard_base_url

logger = logging.getLogger(__name__)


REVIEW_BODY_PREFIX = "Approved in Slack via"


def settings_hint(action: str, path: str = "/my-settings") -> str:
    base = dashboard_base_url()
    return f"{action}: {base}{path}" if base else f"{action} in your Open SWE settings."


def github_token_hint() -> str:
    return settings_hint("Sign in again with GitHub to refresh Open SWE's access")


def github_error(response: httpx2.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    message = body.get("message") if isinstance(body, dict) else None
    text = message if isinstance(message, str) and message else response.text[:200]
    return f"{response.status_code} {text}".strip()


async def _review_body(approval: HumanReviewRequest) -> str:
    # The card is reposted when it is broadcast or closed; the thread root is the stable link.
    anchor = (
        approval.slack_message_ts
        if is_code_channel_session(approval.slack_thread_ts)
        else approval.slack_thread_ts
    )
    permalink = await get_slack_permalink(approval.slack_channel_id, anchor)
    if permalink is None:
        return f"{REVIEW_BODY_PREFIX} Open SWE expedited review."
    return f"{REVIEW_BODY_PREFIX} [expedited review]({permalink})."


async def submit_approval(
    approval: HumanReviewRequest, vote: HumanReviewParticipant, head_sha: str
) -> str | None:
    """POST ``vote`` as its voter's ``APPROVE`` review on ``head_sha``; why it failed, or ``None``.

    Records the review id and SHA on ``vote``; the caller persists them.
    """
    login = vote.github_login
    pr = approval.pull_request
    payload = {"commit_id": head_sha, "event": "APPROVE", "body": await _review_body(approval)}
    try:
        async with GitHubClient.as_user(login) as github:
            data = await github.repo(pr.owner, pr.repo).post(f"pulls/{pr.number}/reviews", payload)
    except GitHubSignInRequired:
        return f"Open SWE has no GitHub token for @{login}. {github_token_hint()}"
    except httpx2.HTTPStatusError as exc:
        return f"GitHub rejected @{login}'s review: {github_error(exc.response)}"
    except httpx2.HTTPError, ValueError:
        return f"GitHub did not answer when submitting @{login}'s review."
    review_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(review_id, int):
        return f"GitHub returned an unexpected review response for @{login}."
    vote.github_review_id = review_id
    vote.github_review_sha = head_sha
    return None


async def dismiss_approval(
    approval: HumanReviewRequest, vote: HumanReviewParticipant, repo: RepoClient, reason: str
) -> None:
    """Withdraw the GitHub review ``vote`` submitted; clears its id once GitHub confirms.

    A review whose id is still set after this is retried the next time a card closes.
    """
    if vote.github_review_id is None:
        return
    path = f"pulls/{approval.pull_request.number}/reviews/{vote.github_review_id}/dismissals"
    payload = {"message": f"Expedited review closed: {reason}", "event": "DISMISS"}
    try:
        await repo.github.request("PUT", f"repos/{repo.full_name}/{path}", json=payload)
    except GitHubError as refused:
        # A review GitHub no longer has cannot count toward a merge either.
        if refused.response.status_code != 404:
            logger.warning(
                "GitHub refused to dismiss an expedited review approval",
                extra={
                    "approval_id": str(approval.id),
                    "github_review_id": vote.github_review_id,
                    "github_message": refused.message,
                },
            )
            return
    except httpx2.HTTPError:
        logger.warning(
            "Failed to dismiss an expedited review approval on GitHub",
            extra={"approval_id": str(approval.id), "github_review_id": vote.github_review_id},
            exc_info=True,
        )
        return
    vote.github_review_id = None
