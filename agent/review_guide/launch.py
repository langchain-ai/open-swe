"""Open a review guide code channel for a pull request, and pause it when the PR moves.

A pull request update never starts a run: the guide posts a pause note and
waits for the reader. A closed guide is not even paused: the guide ended it, or
Slack archived its channel. Only the reader's own message reopens it.
"""

import logging
import time

from langgraph_sdk.client import LangGraphClient
from pydantic import BaseModel, Field

from agent.dashboard.profiles import get_valid_access_token
from agent.dispatch import create_durable_run
from agent.expedited_review.reviews import github_token_hint
from agent.github.pull_requests import PullRequest, PullRequestEvent
from agent.input_messages import build_run_input
from agent.invocation import new_invocation_id, with_invocation_id
from agent.prompts import prompt
from agent.review_guide.github import fetch_head
from agent.review_guide.messages import pause, refresh_progress
from agent.review_guide.sessions import GuideMode, ReviewGuideSession
from agent.sandboxes.tool_access import (
    SANDBOX_HOST_THREAD_KEY,
    SANDBOX_PROXY_CONFIG_METADATA_KEY,
    sandbox_host_thread_id,
)
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
from agent.slack.http import SlackRequestError
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
    # The conversation the walkthrough is forked from, sandbox included.
    source_thread_id: str
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
    thread_id = await _fork(client, start.source_thread_id, login)
    try:
        channel_id = await create_code_channel(
            name=f"Review {start.repo}#{start.number}: {head.title}"[:200],
            session_id=thread_id,
            origin_channel_id=start.origin_channel_id,
            origin_message_ts=start.origin_message_ts,
            team_id=start.team_id,
        )
    except SlackRequestError as exc:
        raise GuideStartError(f"Slack could not create the code channel: {exc}") from exc
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
        try:
            await archive_code_channel(channel_id)
        except SlackRequestError:
            logger.warning(
                "Could not archive an unbound review guide channel",
                extra={"slack_channel": channel_id},
                exc_info=True,
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
    try:
        await set_context_bar(
            channel_id,
            repo_context_bar_items(
                {"owner": start.owner, "name": start.repo},
                pr_url=pull_request.url,
                dashboard_url=dashboard_thread_url(thread_id) or "",
            ),
        )
    except SlackRequestError:
        logger.warning(
            "Could not set a review guide channel's context bar",
            extra={"slack_channel": channel_id},
            exc_info=True,
        )
    session = await ReviewGuideSession.get(thread_id)
    if session is None:
        raise GuideStartError("the review session was not saved")
    await refresh_progress(session, stage="starting")
    await dispatch_guide_run(session, prompt("review-guide/kickoff", pr_number=start.number))
    return StartedGuide(thread_id=thread_id, channel_id=channel_id)


class _ThreadCopy(BaseModel):
    thread_id: str
    metadata: dict[str, object] = {}


class _SourceMetadata(BaseModel):
    sandbox_id: str | None = None
    proxy_config: object = Field(default=None, alias=SANDBOX_PROXY_CONFIG_METADATA_KEY)


async def _fork(client: LangGraphClient, source_thread_id: str, login: str) -> str:
    """A copy of the asking thread that shares its sandbox, so the walkthrough knows its work.

    The copy keeps every checkpoint but none of the source's other metadata: its
    Slack location, title and run bookkeeping belong to the conversation it came
    from. It joins the source's sandbox as a guest, the way other threads borrow one.
    """
    try:
        source = _SourceMetadata.model_validate(
            thread_metadata(await client.threads.get(source_thread_id))
        )
        host = await sandbox_host_thread_id(source_thread_id)
        copied = _ThreadCopy.model_validate(await client.threads.copy(source_thread_id))
        await client.threads.update(
            thread_id=copied.thread_id,
            metadata={
                **dict.fromkeys(copied.metadata),
                "visibility": "public",
                "owner_type": "user",
                "owner_login": login,
                "created_at_ms": int(time.time() * 1000),
                **(
                    {
                        SANDBOX_HOST_THREAD_KEY: host,
                        "sandbox_id": source.sandbox_id,
                        SANDBOX_PROXY_CONFIG_METADATA_KEY: source.proxy_config,
                    }
                    if source.sandbox_id
                    else {}
                ),
            },
        )
    except Exception as exc:
        logger.exception(
            "Could not fork the thread asking for a review walkthrough",
            extra={"agent_thread_id": source_thread_id},
        )
        raise GuideStartError("could not copy this conversation into the review channel") from exc
    logger.info(
        "Forked a thread into a review walkthrough",
        extra={"agent_thread_id": copied.thread_id, "source_thread_id": source_thread_id},
    )
    return copied.thread_id


async def dispatch_guide_run(
    session: ReviewGuideSession,
    text: str,
    *,
    prefetch: bool = False,
    approve_ts: str = "",
) -> None:
    """Start a walkthrough turn no person typed, so it cannot approve the pull request.

    A prefetch turn prepares chunks in the background: it shows nothing, so it
    leaves the session's status alone. ``approve_ts`` names a message whose
    "Looks good" the turn records before the model runs.
    """
    location = SlackThreadRef(
        channel_id=session.slack_channel_id, thread_ts=CODE_CHANNEL_SESSION_TS
    )
    reader = await User.get(session.user_id)
    pr = session.pull_request
    # Set every run: a thread carries its last run's configurable into the next.
    configurable: dict[str, object] = {
        "thread_id": session.thread_id,
        "repo": {"owner": pr.owner, "name": pr.repo},
        "slack_thread": location.dump(),
        "source": "slack",
        "review_guide_prefetch": prefetch,
        "review_guide_approve_ts": approve_ts,
    }
    if reader is not None and reader.github_login:
        configurable["github_login"] = reader.github_login
    if session.workspace_slug:
        configurable["workspace"] = session.workspace_slug
    if not prefetch:
        await set_session_status(session.slack_channel_id, "processing")
    await create_durable_run(
        session.thread_id,
        "agent",
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
