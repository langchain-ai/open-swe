"""The GitHub review behind each expedited approval, from the click until the card closes."""

import logging

import httpx2

from agent.dashboard.profiles import get_valid_access_token
from agent.expedited_review.approvals import ApprovalVote, ExpeditedApproval
from agent.github.http import GITHUB_API_BASE, github_client, github_request
from agent.slack.client import get_slack_permalink
from agent.slack.code_channels import is_code_channel_session
from agent.utils.dashboard_links import dashboard_base_url

logger = logging.getLogger(__name__)


def settings_hint(action: str) -> str:
    base = dashboard_base_url()
    return f"{action}: {base}/my-settings" if base else f"{action} in your Open SWE settings."


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


async def _review_body(approval: ExpeditedApproval) -> str:
    # The card is reposted when it is broadcast or closed; the thread root is the stable link.
    anchor = (
        approval.slack_message_ts
        if is_code_channel_session(approval.slack_thread_ts)
        else approval.slack_thread_ts
    )
    permalink = await get_slack_permalink(approval.slack_channel_id, anchor)
    if permalink is None:
        return "Approved in Slack via Open SWE expedited review."
    return f"Approved in Slack via [expedited review]({permalink})."


async def submit_approval(
    approval: ExpeditedApproval, vote: ApprovalVote, head_sha: str
) -> str | None:
    """POST ``vote`` as its voter's ``APPROVE`` review on ``head_sha``; why it failed, or ``None``.

    Records the review id and SHA on ``vote``; the caller persists them.
    """
    login = vote.github_login
    user_token = await get_valid_access_token(login)
    if not user_token:
        return f"Open SWE has no GitHub token for @{login}. {github_token_hint()}"
    pr = approval.pull_request
    url = f"{GITHUB_API_BASE}/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}/reviews"
    payload = {"commit_id": head_sha, "event": "APPROVE", "body": await _review_body(approval)}
    try:
        async with github_client(token=user_token) as client:
            response = await github_request(client, "POST", url, json=payload)
            response.raise_for_status()
            data = response.json()
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
    approval: ExpeditedApproval, vote: ApprovalVote, token: str, reason: str
) -> None:
    """Withdraw the GitHub review ``vote`` submitted, if it submitted one."""
    if vote.github_review_id is None:
        return
    pr = approval.pull_request
    url = (
        f"{GITHUB_API_BASE}/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}/reviews/"
        f"{vote.github_review_id}/dismissals"
    )
    payload = {"message": f"Expedited review closed: {reason}", "event": "DISMISS"}
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "PUT", url, json=payload)
            response.raise_for_status()
    except httpx2.HTTPError:
        logger.warning(
            "Failed to dismiss an expedited review approval on GitHub",
            extra={"approval_id": str(approval.id), "github_review_id": vote.github_review_id},
            exc_info=True,
        )


async def dismiss_approvals(approval: ExpeditedApproval, token: str, reason: str) -> None:
    """Withdraw every GitHub review a closed, unmerged card submitted."""
    for vote in approval.approvals:
        await dismiss_approval(approval, vote, token, reason)
