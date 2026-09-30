"""Watch pull requests people post in their repository's Slack review channel.

A top-level message linking exactly one pull request, from someone with
``review_channel_watch`` on, in the channel that repository names as its
``reviewChannel``, becomes a ``posted`` request on that message.
"""

import logging
import re

from sqlalchemy.exc import IntegrityError

from agent.github.repo_files import RepoSettings
from agent.human_review.people import repo_token
from agent.human_review.requests import HumanReviewRequest
from agent.human_review.standard import record_pull_request, settle
from agent.slack.channels import SlackChannel
from agent.slack.client import GitHubPrRef, parse_github_pr_url
from agent.users import User

logger = logging.getLogger(__name__)

_PR_URL = re.compile(r"https?://(?:www\.)?github\.com/[^\s|<>/]+/[^\s|<>/]+/pull/\d+", re.I)


def linked_pull_request(text: str) -> GitHubPrRef | None:
    """The one pull request a message links; ``None`` when it links none or several."""
    refs = {
        ref.url.lower(): ref
        for match in _PR_URL.finditer(text)
        if (ref := parse_github_pr_url(match.group(0))) is not None
    }
    return next(iter(refs.values())) if len(refs) == 1 else None


async def _is_review_channel(pr_ref: GitHubPrRef, channel_id: str, token: str) -> bool:
    configured = (await RepoSettings.cached(pr_ref.owner, pr_ref.repo, token=token)).review_channel
    if not configured.strip():
        return False
    channel = await SlackChannel.resolve(configured)
    return channel is not None and channel.id == channel_id


async def watch_post(channel_id: str, message_ts: str, slack_user_id: str, text: str) -> None:
    """Start watching the pull request ``text`` links, if the message qualifies."""
    pr_ref = linked_pull_request(text)
    if pr_ref is None:
        return
    user = await User.for_identity("slack", slack_user_id)
    if user is None or not user.typed_preferences.review_channel_watch:
        return
    extra = {
        "pr_repo_full_name": f"{pr_ref.owner}/{pr_ref.repo}",
        "pr_number": pr_ref.number,
        "slack_channel": channel_id,
        "slack_message_ts": message_ts,
    }
    token = await repo_token(pr_ref.owner, pr_ref.repo)
    if token is None:
        logger.info("Posted pull request is outside the GitHub App's reach", extra=extra)
        return
    if not await _is_review_channel(pr_ref, channel_id, token):
        return
    if await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number) is not None:
        logger.info("Posted pull request already has an open review request", extra=extra)
        return
    recorded = await record_pull_request(pr_ref, token)
    if recorded is None:
        logger.warning("Could not read a pull request posted for review", extra=extra)
        return
    pull_request, details = recorded
    if details.state != "open":
        return
    try:
        request = await HumanReviewRequest(
            pull_request_id=pull_request.id,
            head_sha=details.head_sha,
            kind="posted",
            requested_by_user_id=user.id,
            slack_channel_id=channel_id,
            slack_message_ts=message_ts,
        ).save()
    except IntegrityError:
        logger.info("Posted pull request gained a review request meanwhile", extra=extra)
        return
    logger.info(
        "Watching a pull request posted for review", extra={**extra, "request_id": str(request.id)}
    )
    await settle(request)
