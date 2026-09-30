"""Open a review guide code channel for a pull request, and pause it when the PR moves.

A pull request update never starts a run: the guide posts a pause note and
waits for the reader. A closed guide is not even paused: the guide ended it, or
Slack archived its channel. Only the reader's own message reopens it.
"""

import logging
import time
from uuid import uuid4

from langgraph_sdk.client import LangGraphClient
from pydantic import BaseModel

from agent.dashboard.profiles import get_valid_access_token
from agent.dispatch import create_durable_run
from agent.expedited_review.reviews import github_token_hint
from agent.github.pull_requests import PullRequest, PullRequestEvent
from agent.input_messages import build_run_input
from agent.invocation import new_invocation_id, with_invocation_id
from agent.prompts import prompt
from agent.review_guide.github import fetch_head
from agent.review_guide.messages import pause, refresh_progress
from agent.review_guide.sessions import ASSISTANT_ID, GuideMode, ReviewGuideSession
from agent.slack.channels import SlackChannel
from agent.slack.client import bind_slack_thread_id, invite_to_slack_channel, slack_user_ids
from agent.slack.code_channels import (
    CODE_CHANNEL_SESSION_TS,
    archive_code_channel,
    create_code_channel,
    repo_context_bar_items,
    set_context_bar,
    set_session_status,
)
from agent.source_context import SlackThreadRef, SourceContext
from agent.users import User
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.json_types import thread_metadata
from agent.utils.thread_ops import langgraph_client
from agent.webhooks.common import upsert_agent_thread_metadata

logger = logging.getLogger(__name__)

_SENDER_ID = "system:review-guide"
PREFETCH_KIND = "review_guide_prefetch"


class GuideStart(BaseModel):
    owner: str
    repo: str
    number: int
    requester_slack_id: str
    origin_channel_id: str
    origin_message_ts: str
    team_id: str = ""
    workspace_slug: str | None = None
    invite: list[str] = []


class StartedGuide(BaseModel):
    thread_id: str
    channel_id: str


class GuideStartError(RuntimeError):
    """The review guide could not be opened; the message says why."""


async def start_review_guide(start: GuideStart) -> StartedGuide:
    user = await User.for_identity("slack", start.requester_slack_id)
    login = user.github_login if user else ""
    if user is None or not login:
        raise GuideStartError("the requester has no GitHub account linked to Open SWE")
    # The channel shows the PR's code, so only someone who can read it may open one.
    user_token = await get_valid_access_token(login)
    if not user_token:
        raise GuideStartError(f"Open SWE has no GitHub token for @{login}. {github_token_hint()}")
    head = await fetch_head(start.owner, start.repo, start.number, token=user_token)
    if head is None:
        raise GuideStartError(f"@{login} cannot read that pull request on GitHub")
    if head.state != "open":
        raise GuideStartError("the pull request is not open")
    pull_request = await PullRequest(
        owner=start.owner, repo=start.repo, number=start.number
    ).ensure()
    mode: GuideMode = (
        "author"
        if head.author.lower() == login.lower() or await pull_request.is_authored_by(login)
        else "reviewer"
    )
    client = langgraph_client()
    thread_id = await _fork_builder(client, pull_request, login) or str(uuid4())
    channel_id, error = await create_code_channel(
        name=f"Review {start.repo}#{start.number}: {head.title}"[:200],
        session_id=thread_id,
        origin_channel_id=start.origin_channel_id,
        origin_message_ts=start.origin_message_ts,
        team_id=start.team_id,
    )
    if channel_id is None:
        raise GuideStartError(error or "Slack could not create the code channel")
    location = SlackThreadRef(
        channel_id=channel_id,
        thread_ts=CODE_CHANNEL_SESSION_TS,
        triggering_event_ts=start.origin_message_ts,
        team_id=start.team_id,
    )
    try:
        if not await upsert_agent_thread_metadata(
            thread_id,
            source="slack",
            repo_config={"owner": start.owner, "name": start.repo},
            github_login=login,
            title=f"Review {start.repo}#{start.number}: {head.title}",
            static_title=True,
            source_context=SourceContext(slack_thread=location),
            workspace=start.workspace_slug,
            owner_login=login,
            # Reachable from the channel's web link, but read-only there, so kept out of lists.
            unlisted=True,
        ):
            raise GuideStartError("could not create the review thread")
        await bind_slack_thread_id(client, channel_id, CODE_CHANNEL_SESSION_TS, thread_id)
        await ReviewGuideSession.create(
            thread_id=thread_id,
            pull_request=pull_request,
            user_id=user.id,
            slack_channel_id=channel_id,
            workspace_slug=start.workspace_slug,
            mode=mode,
        )
    except Exception:
        if not (await archive_code_channel(channel_id))[0]:
            logger.warning(
                "Could not archive an unbound review guide channel",
                extra={"slack_channel": channel_id},
            )
        raise
    _, invite_error = await invite_to_slack_channel(
        channel_id, slack_user_ids([start.requester_slack_id, *start.invite])
    )
    if invite_error:
        logger.warning(
            "Could not invite everyone to a review guide channel",
            extra={"slack_channel": channel_id, "slack_error": invite_error},
        )
    await set_context_bar(
        channel_id,
        repo_context_bar_items(
            {"owner": start.owner, "name": start.repo},
            pr_url=pull_request.url,
            dashboard_url=dashboard_thread_url(thread_id) or "",
        ),
    )
    session = await ReviewGuideSession.get(thread_id)
    if session is not None:
        await refresh_progress(session, stage="starting")
    await dispatch_guide_run(
        thread_id,
        location,
        prompt("review-guide/kickoff", pr_number=start.number),
        workspace_slug=start.workspace_slug,
    )
    return StartedGuide(thread_id=thread_id, channel_id=channel_id)


class _ThreadCopy(BaseModel):
    thread_id: str
    metadata: dict[str, object] = {}


class _BuilderMetadata(BaseModel):
    visibility: str | None = None


async def _fork_builder(
    client: LangGraphClient, pull_request: PullRequest, login: str
) -> str | None:
    """A copy of the thread that built the PR, so the guide remembers why, or ``None``.

    The copy keeps every checkpoint but none of the builder's metadata: an
    inherited ``sandbox_id`` or Slack location would point the guide at the
    builder's sandbox and channel. Private builders are never copied, because
    the guide talks in a channel anyone can join.
    """
    builder = pull_request.agent_thread_id or pull_request.primary_thread_id
    if not builder:
        return None
    try:
        source = _BuilderMetadata.model_validate(thread_metadata(await client.threads.get(builder)))
        if source.visibility == "private":
            return None
        copied = _ThreadCopy.model_validate(await client.threads.copy(builder))
        await client.threads.update(
            thread_id=copied.thread_id,
            metadata={
                **dict.fromkeys(copied.metadata),
                "visibility": "public",
                "owner_type": "user",
                "owner_login": login,
                "created_at_ms": int(time.time() * 1000),
            },
        )
    except Exception:
        logger.exception(
            "Could not fork the thread that built a pull request",
            extra={"agent_thread_id": builder, "pr_number": pull_request.number},
        )
        return None
    logger.info(
        "Forked the builder thread for a review guide",
        extra={"agent_thread_id": copied.thread_id, "builder_thread_id": builder},
    )
    return copied.thread_id


async def dispatch_guide_run(
    thread_id: str,
    location: SlackThreadRef,
    text: str,
    *,
    workspace_slug: str | None,
    prefetch: bool = False,
    approve_ts: str = "",
) -> None:
    """Start a guide turn no person typed, so it cannot approve the pull request.

    A prefetch turn prepares chunks in the background: it shows nothing, so it
    leaves the session's status alone. ``approve_ts`` names a message whose
    "Looks good" the turn records before the model runs.
    """
    # Set every run: a thread carries its last run's configurable into the next.
    configurable: dict[str, object] = {
        "thread_id": thread_id,
        "slack_thread": location.dump(),
        "source": "slack",
        "review_guide_prefetch": prefetch,
        "review_guide_approve_ts": approve_ts,
    }
    if workspace_slug:
        configurable["workspace"] = workspace_slug
    if not prefetch and location.channel_id:
        await set_session_status(location.channel_id, "processing")
    await create_durable_run(
        thread_id,
        ASSISTANT_ID,
        input=build_run_input(
            text,
            {"sender_id": _SENDER_ID, "surface": "automation", "kind": "system"},
            systems=[{"id": _SENDER_ID, "display_name": "Review guide", "platform": "open-swe"}],
        ),
        source="review-guide",
        thread_title=None,
        config={"configurable": with_invocation_id(configurable, new_invocation_id())},
        metadata={"kind": PREFETCH_KIND} if prefetch else None,
        multitask_strategy="enqueue",
    )


class _ChannelState(BaseModel):
    is_archived: bool = False


async def channel_archived(channel_id: str) -> bool:
    channel = await SlackChannel.fetch(channel_id, use_cache=False)
    return channel is not None and _ChannelState.model_validate(channel).is_archived


async def close_guide_for_channel(channel_id: str) -> None:
    """Close the guide bound to a channel Slack just archived, if there is one."""
    session = await ReviewGuideSession.for_channel(channel_id)
    if session is not None and not session.closed:
        await session.set_closed(True)
        logger.info(
            "Closed a review guide whose channel was archived",
            extra={"agent_thread_id": session.thread_id, "slack_channel": channel_id},
        )


async def notify_pr_updated(payload: dict[str, object]) -> None:
    """Pause every open guide on a pull request that just moved, until its reader goes on.

    No run starts: the guide picks the new head up on the reader's next message.
    """
    event = PullRequestEvent.parse(payload)
    identity = event.identity if event else None
    if event is None or identity is None:
        return
    owner, repo, number = identity
    for session in await ReviewGuideSession.for_pull_request(owner, repo, number):
        if session.closed:
            continue
        if await channel_archived(session.slack_channel_id):
            await session.set_closed(True)
            continue
        try:
            await pause(session, event.pull_request.head_sha)
            await refresh_progress(session, session.walk, stage="paused")
        except Exception:
            logger.exception(
                "Could not pause a review guide for an updated pull request",
                extra={"agent_thread_id": session.thread_id, "pr_number": number},
            )
