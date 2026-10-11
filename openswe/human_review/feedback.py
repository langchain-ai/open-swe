"""Tell a review request's Slack thread when someone comments on or asks for changes to its PR."""

import logging
import re

import httpx2
from pydantic import BaseModel, ValidationError

from openswe.github.http import GITHUB_API_BASE, github_client, github_request
from openswe.github.pull_requests import PullRequest
from openswe.human_review.people import repo_token
from openswe.human_review.requests import HumanReviewRequest, slack_mention
from openswe.prompts import prompt
from openswe.slack.blocks import escape
from openswe.slack.client import post_slack_thread_reply_with_ts, slack_thread_mutation_lock
from openswe.slack.code_channels import is_code_channel_session
from openswe.slack.dm import CONCIERGE_TS, note_for_concierge
from openswe.store import get_value, put_value
from openswe.users import User
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

_QUOTE_MAX_CHARS = 300
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


class _Account(BaseModel):
    login: str
    type: str = "User"


class _Repository(BaseModel):
    name: str
    owner: _Account


class _PullRequest(BaseModel):
    number: int
    user: _Account


class _Review(BaseModel):
    id: int
    state: str
    html_url: str
    body: str | None = None
    user: _Account | None = None


class _ReviewEvent(BaseModel):
    action: str
    repository: _Repository
    pull_request: _PullRequest
    review: _Review


class _ReviewComment(BaseModel):
    in_reply_to_id: int | None = None


async def _new_threads(request: HumanReviewRequest, review_id: int) -> int | None:
    """How many comments in a review start a thread rather than reply; ``None`` if unreadable."""
    pr = request.pull_request
    token = await repo_token(pr.owner, pr.repo)
    if token is None:
        return None
    url = (
        f"{GITHUB_API_BASE}/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}/reviews/"
        f"{review_id}/comments"
    )
    try:
        async with github_client(token=token) as client:
            response = await github_request(client, "GET", url, params={"per_page": "100"})
            response.raise_for_status()
            comments = [_ReviewComment.model_validate(item) for item in response.json()]
    except httpx2.HTTPError, ValueError, ValidationError:
        logger.warning(
            "Failed to read the comments of a GitHub review",
            extra={"request_id": str(request.id), "github_review_id": review_id},
            exc_info=True,
        )
        return None
    return sum(1 for comment in comments if comment.in_reply_to_id is None)


def _quote(body: str) -> str:
    text = body if len(body) <= _QUOTE_MAX_CHARS else body[:_QUOTE_MAX_CHARS].rstrip() + "…"
    return "\n".join(f">{escape(line)}" for line in text.splitlines())


def _thread_ts(request: HumanReviewRequest) -> str:
    # A code channel's thread ts names its session; the card itself is the stable anchor there.
    if request.slack_thread_ts and not is_code_channel_session(request.slack_thread_ts):
        return request.slack_thread_ts
    return request.slack_message_ts


def _is_concierge_dm(request: HumanReviewRequest) -> bool:
    """Whether the notice's destination is a DM the author runs in concierge mode.

    ``expedite_pr_approval`` keeps the run's Slack location, so an expedited card
    in such a DM has the DM channel with the concierge timestamp.
    """
    return request.kind == "expedited" and request.slack_thread_ts == CONCIERGE_TS


async def _author_slack_user_id(request: HumanReviewRequest) -> str:
    """The PR author's Slack member id, or ``""`` when they are not linked."""
    pr = request.pull_request
    author = (
        await User.get(pr.author_user_id)
        if pr.author_user_id is not None
        else await User.for_login("github", pr.author)
    )
    return author.slack_user_id if author is not None else ""


async def _concierge_author(request: HumanReviewRequest, text: str, review_url: str) -> bool:
    """Queue the notice for the author's concierge thread; ``False`` if there is none.

    A concierge DM keeps no per-message threads and Slack's webhook ignores this
    bot's own posts, so the card's reply would never reach the concierge thread.
    """
    slack_user_id = await _author_slack_user_id(request)
    if not slack_user_id:
        return False
    await note_for_concierge(
        slack_user_id,
        request.slack_channel_id,
        prompt("slack/concierge-github-review-notice", review_text=text, review_url=review_url),
    )
    return True


async def announce_review(payload: dict[str, object]) -> None:
    """Mention the author in the request's thread when a human comments or requests changes."""
    try:
        event = _ReviewEvent.model_validate(payload)
    except ValidationError:
        logger.info("GitHub review webhook has an unexpected shape", exc_info=True)
        return
    review = event.review
    state = review.state.upper()
    if (
        event.action != "submitted"
        or state not in {"COMMENTED", "CHANGES_REQUESTED"}
        or review.user is None
        or review.user.type == "Bot"
        or review.user.login.lower() == event.pull_request.user.login.lower()
    ):
        return
    owner, repo = event.repository.owner.login, event.repository.name
    request = await HumanReviewRequest.active_for(owner, repo, event.pull_request.number)
    if request is None or not request.slack_channel_id or not request.slack_message_ts:
        return
    login = review.user.login
    # A request in its agent's thread already hears a registered reviewer's review from that agent.
    if (
        request.thread_id
        and request.slack_thread_ts
        and await User.known_logins([login])
        and (stored := await PullRequest.get(owner, repo, event.pull_request.number)) is not None
        and stored.agent_thread_id == request.thread_id
    ):
        return
    body = _HTML_COMMENT.sub("", review.body or "").strip()
    if state == "CHANGES_REQUESTED":
        verb = "requested changes on"
    elif body:
        verb = "commented on"
    else:
        threads = await _new_threads(request, review.id)
        if threads == 0:
            return
        verb = "left a comment on" if threads == 1 else "left comments on"

    namespace = ("human_review_feedback_notices", str(request.id))
    key = str(review.id)
    reviewer = slack_mention(await User.for_login("github", login), login)
    label = escape(f"{owner}/{repo}#{event.pull_request.number}")
    text = f"{await request.author_mention()} {reviewer} <{review.html_url}|{verb}> {label}"
    if body:
        text += "\n" + _quote(body)
    # Two deliveries of the same review may overlap; the lock elects one sender.
    try:
        async with slack_thread_mutation_lock(
            langgraph_client(),
            request.slack_channel_id,
            _thread_ts(request),
            purpose=f"human-review:{request.id}",
        ):
            if await get_value(namespace, key) is not None:
                return
            await put_value(namespace, key, {"review_url": review.html_url})
            await _deliver_notice(request, text, review.html_url)
    except Exception:
        logger.warning(
            "Failed to announce a GitHub review to a review request thread",
            extra={"request_id": str(request.id)},
            exc_info=True,
        )


async def _deliver_notice(request: HumanReviewRequest, text: str, review_url: str) -> None:
    """Post the notice where the request's card lives, mirroring it into a concierge DM."""
    if _is_concierge_dm(request) and await _concierge_author(request, text, review_url):
        return
    message_ts, error = await post_slack_thread_reply_with_ts(
        request.slack_channel_id,
        _thread_ts(request),
        text,
        agent_thread_id=request.thread_id or None,
        unfurl_links=False,
        unfurl_media=False,
    )
    if message_ts is None:
        logger.warning(
            "Failed to post a GitHub review notice to a review request thread",
            extra={"request_id": str(request.id), "slack_error": error},
        )
