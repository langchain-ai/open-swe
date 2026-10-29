"""Watch pull requests people post in any Slack channel the bot listens in."""

import logging
import re

from sqlalchemy.exc import IntegrityError

from openswe.github.http import GitHubAppUnavailable
from openswe.github.pull_request_status import PullRequestClient
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import record_pull_request, settle
from openswe.slack.client import GitHubPrRef, parse_github_pr_url
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_review_pr_url
from openswe.utils.preview import skip_on_preview

logger = logging.getLogger(__name__)

_URL = re.compile(r"https?://[^\s|<>]+", re.I)


def linked_pull_requests(text: str) -> list[GitHubPrRef]:
    """Distinct GitHub PRs linked directly or through this deployment's review page."""
    refs: dict[str, GitHubPrRef] = {}
    for match in _URL.finditer(text):
        url = match.group().rstrip(".,;:!?)]\"'")
        try:
            ref = parse_github_pr_url(dashboard_review_pr_url(url) or url)
        except ValueError:
            logger.debug("Ignoring malformed PR link", exc_info=True)
            continue
        if ref is not None and ref.number > 0:
            refs[ref.url.lower()] = ref
    return list(refs.values())


async def watch_post(channel_id: str, message_ts: str, slack_user_id: str, text: str) -> None:
    """Start watching each pull request linked by a qualifying message."""
    if skip_on_preview("watch_post"):
        return
    refs = linked_pull_requests(text)
    if not refs:
        return
    user = await User.for_identity("slack", slack_user_id)
    if user is None:
        return
    requests: list[HumanReviewRequest] = []
    for ref in refs:
        if (
            request := await _watch_pull_request(channel_id, message_ts, user, ref, refs)
        ) is not None:
            requests.append(request)
    if not requests:
        return
    async with HumanReviewRequest.locked_post(channel_id, message_ts) as rows:
        urls = [row.pull_request.url.lower() for row in rows]
        for row in rows:
            row.run_config = {**row.run_config, "posted_pr_urls": urls}
    for request in requests:
        await ReviewCard(request).link_on_github()
        await settle(request)


async def _watch_pull_request(
    channel_id: str,
    message_ts: str,
    user: User,
    pr_ref: GitHubPrRef,
    refs: list[GitHubPrRef],
) -> HumanReviewRequest | None:
    extra = {
        "pr_repo_full_name": f"{pr_ref.owner}/{pr_ref.repo}",
        "pr_number": pr_ref.number,
        "slack_channel": channel_id,
        "slack_message_ts": message_ts,
    }
    if await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number) is not None:
        logger.info("Posted pull request already has an open review request", extra=extra)
        return
    try:
        async with PullRequestClient.as_app(pr_ref.owner, pr_ref.repo, pr_ref.number) as pull:
            recorded = await record_pull_request(pull)
    except GitHubAppUnavailable:
        logger.info("Posted pull request is outside the GitHub App's reach", extra=extra)
        return
    if recorded is None:
        logger.warning("Could not read a pull request posted for review", extra=extra)
        return
    pull_request, details = recorded
    if await User.for_login("github", details.author) is None:
        return
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
            run_config={"posted_pr_urls": [ref.url.lower() for ref in refs]},
        ).save()
    except IntegrityError:
        logger.info("Posted pull request gained a review request meanwhile", extra=extra)
        return
    logger.info(
        "Watching a pull request posted for review", extra={**extra, "request_id": str(request.id)}
    )
    return request
