"""Start an interactive review of a pull request, and pause it when the PR moves.

The review runs in the conversation that asked for it, a Slack thread or the
dashboard, or in a code channel of its own, forked from that conversation.

A pull request update never starts a run: the guide posts a pause note and
waits for the reader. A closed guide is not even paused: the guide ended it, or
Slack archived its channel. Only the reader's own message reopens it.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

from openswe.dashboard.profiles import get_valid_access_token
from openswe.dispatch import create_durable_run
from openswe.expedited_review.reviews import github_token_hint, settings_hint
from openswe.github.pull_requests import PullRequest, PullRequestEvent
from openswe.input_messages import build_run_input
from openswe.invocation import new_invocation_id, with_invocation_id
from openswe.prompts import prompt
from openswe.review_guide.github import PullRequestHead, fetch_head
from openswe.review_guide.messages import pause, refresh_progress
from openswe.review_guide.sessions import GuideMode, ReviewGuideSession
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_access import (
    SANDBOX_HOST_THREAD_KEY,
    SANDBOX_PROXY_CONFIG_METADATA_KEY,
    sandbox_host_thread_id,
)
from openswe.slack.channels import SlackChannel
from openswe.slack.client import invite_to_slack_channel
from openswe.slack.code_channels import (
    CODE_CHANNEL_SESSION_TS,
    archive_code_channel,
    create_code_channel,
    repo_context_bar_items,
    set_context_bar,
    set_session_status,
)
from openswe.slack.dm import post_dm
from openswe.slack.http import SlackRequestError
from openswe.slack.records import bind_slack_thread_id
from openswe.source_context import SlackThreadRef, SourceContext
from openswe.threads.store import THREADS
from openswe.threads.summary import DASHBOARD_SOURCE, thread_is_private
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.webhooks.common import upsert_agent_thread_metadata

logger = logging.getLogger(__name__)

_SENDER_ID = "system:review-guide"
# The run source of a turn the guide starts itself, which no person typed.
GUIDE_RUN_SOURCE = "review-guide"


class GuideStartError(RuntimeError):
    """The interactive review could not be started; the message says why."""


class GuideOrigin(BaseModel):
    """Who asked for an interactive review, and from where."""

    requester_id: UUID
    team_id: str = ""
    # The Slack thread asked from; None from the dashboard or from outside a conversation.
    slack_thread: SlackThreadRef | None = None
    # The conversation asked from, sandbox included; None when there is none.
    source_thread_id: str | None = None
    workspace_slug: str | None = None
    is_private: bool = False

    @classmethod
    async def for_run(cls, cfg: RunConfig) -> Self:
        """The conversation the current run is in, and the person whose message started it."""
        if cfg.mcp_caller:
            return await cls.for_mcp_caller(cfg.mcp_caller)
        on_web = cfg.source == DASHBOARD_SOURCE or cfg.slack_thread is None
        requester = (
            await User.for_login("github", cfg.github_login)
            if on_web and cfg.github_login
            else await User.for_identity("slack", cfg.slack_thread.triggering_user_id)
            if cfg.slack_thread is not None and cfg.slack_thread.triggering_user_id
            else None
        )
        if not cfg.thread_id or requester is None:
            raise GuideStartError("only a person's own message can start an interactive review")
        thread = await THREADS.get(cfg.thread_id)
        if thread is None:
            raise GuideStartError("Cannot verify thread credential scope")
        slack_thread = None if on_web else SourceContext.from_metadata(thread.metadata).slack_thread
        if not on_web and (slack_thread is None or slack_thread.location is None):
            raise GuideStartError("Current Slack location is unavailable")
        return cls(
            requester_id=requester.id,
            team_id=cfg.slack_thread.team_id if cfg.slack_thread else "",
            slack_thread=slack_thread,
            source_thread_id=cfg.thread_id,
            workspace_slug=cfg.workspace_slug,
            is_private=thread_is_private(thread.metadata)
            or (slack_thread is not None and slack_thread.channel_id.startswith("D")),
        )

    @classmethod
    async def for_mcp_caller(cls, login: str) -> Self:
        """A person calling over MCP: no conversation to review in, and as private as their DM."""
        user = await User.for_login("github", login)
        if user is None or not user.slack_user_id:
            raise GuideStartError(
                "Your Slack account is not linked to Open SWE. "
                + settings_hint("Connect Slack under Connections", "/my-settings/connections")
            )
        return cls(requester_id=user.id, team_id=user.slack_team_id, is_private=True)

    async def message(
        self, requester: User, pull_request: PullRequest, title: str
    ) -> tuple[str, str]:
        """The Slack message a code channel opens from, DMing the requester when there is none."""
        if self.slack_thread is not None and self.slack_thread.channel_id:
            return (
                self.slack_thread.channel_id,
                self.slack_thread.triggering_event_ts or self.slack_thread.thread_ts,
            )
        if not requester.slack_user_id:
            raise GuideStartError(
                "a code channel needs your Slack account linked to Open SWE. "
                + settings_hint("Connect Slack under Connections", "/my-settings/connections")
            )
        try:
            return await post_dm(
                requester.slack_user_id,
                f"Opening a review channel for "
                f"<{pull_request.url}|{pull_request.repo}#{pull_request.number}>: {title}",
            )
        except SlackRequestError as exc:
            raise GuideStartError(f"Slack could not DM the requester: {exc}") from exc


class GuideStart(BaseModel):
    owner: str
    repo: str
    number: int
    origin: GuideOrigin
    # Open a code channel for it, instead of reviewing in the conversation that asked.
    code_channel: bool = False
    invite: list[str] = []


class StartedGuide(BaseModel):
    thread_id: str
    # The code channel opened for it; empty when it runs in the conversation that asked.
    channel_id: str = ""


@dataclass
class GuideLaunch:
    """A start request checked against GitHub: who reads, what, and as author or reviewer."""

    start: GuideStart
    requester: User
    login: str
    head: PullRequestHead
    pull_request: PullRequest
    mode: GuideMode

    @classmethod
    async def check(cls, start: GuideStart) -> Self:
        requester = await User.get(start.origin.requester_id)
        login = requester.github_login if requester else ""
        if requester is None or not login:
            raise GuideStartError("the requester has no GitHub account linked to Open SWE")
        # The review shows the PR's code, so only someone who can read it may start one.
        user_token = await get_valid_access_token(login)
        if not user_token:
            raise GuideStartError(
                f"Open SWE has no GitHub token for @{login}. {github_token_hint()}"
            )
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
        return cls(
            start=start,
            requester=requester,
            login=login,
            head=head,
            pull_request=pull_request,
            mode=mode,
        )

    @property
    def title(self) -> str:
        return f"Review {self.start.repo}#{self.start.number}: {self.head.title}"

    async def record(self, thread_id: str, location: SlackThreadRef | None) -> None:
        await ReviewGuideSession.create(
            thread_id=thread_id,
            pull_request=self.pull_request,
            user_id=self.requester.id,
            location=location,
            workspace_slug=self.start.origin.workspace_slug,
            mode=self.mode,
        )

    async def begin(self, thread_id: str) -> StartedGuide:
        """Start the recorded session's first turn."""
        session = await ReviewGuideSession.get(thread_id)
        if session is None:
            raise GuideStartError("the review session was not saved")
        await refresh_progress(session, stage="starting")
        await dispatch_guide_run(
            session,
            prompt(
                "review-guide/kickoff",
                pr_number=self.start.number,
                surface=session.surface,
            ),
        )
        return StartedGuide(
            thread_id=thread_id,
            channel_id=session.slack_channel_id if session.in_code_channel else "",
        )

    async def here(self) -> StartedGuide:
        """Review in the conversation that asked; its first turn runs once the current one ends."""
        origin = self.start.origin
        if not origin.source_thread_id:
            raise GuideStartError("an interactive review here needs a conversation to run in")
        location = (
            SlackThreadRef(
                channel_id=origin.slack_thread.channel_id,
                thread_ts=origin.slack_thread.thread_ts,
            )
            if origin.slack_thread is not None and origin.slack_thread.location
            else None
        )
        await self.record(origin.source_thread_id, location)
        return await self.begin(origin.source_thread_id)

    async def code_channel(self) -> StartedGuide:
        """Fork the conversation that asked into a new code channel, and review there."""
        start, origin = self.start, self.start.origin
        origin_channel_id, origin_message_ts = await origin.message(
            self.requester, self.pull_request, self.head.title
        )
        thread_id = (
            await _fork(origin.source_thread_id, self.login, is_private=origin.is_private)
            if origin.source_thread_id
            else str(uuid.uuid4())
        )
        try:
            channel_id = await create_code_channel(
                name=self.title[:200],
                session_id=thread_id,
                origin_channel_id=origin_channel_id,
                origin_message_ts=origin_message_ts,
                team_id=origin.team_id,
                is_private=origin.is_private,
            )
        except SlackRequestError as exc:
            raise GuideStartError(f"Slack could not create the code channel: {exc}") from exc
        location = SlackThreadRef(
            channel_id=channel_id,
            thread_ts=CODE_CHANNEL_SESSION_TS,
            triggering_event_ts=origin_message_ts,
            team_id=origin.team_id,
        )
        try:
            if not await upsert_agent_thread_metadata(
                thread_id,
                source="slack",
                repo_config={"owner": start.owner, "name": start.repo},
                github_login=self.login,
                title=self.title,
                static_title=True,
                source_context=SourceContext(slack_thread=location),
                workspace=origin.workspace_slug,
                visibility="private" if origin.is_private else "public",
                owner_login=self.login,
            ):
                raise GuideStartError("could not create the review thread")
            await bind_slack_thread_id(channel_id, CODE_CHANNEL_SESSION_TS, thread_id)
            await self.record(thread_id, location)
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
        try:
            await invite_to_slack_channel(channel_id, [self.requester.slack_user_id, *start.invite])
        except SlackRequestError as exc:
            logger.warning(
                "Could not invite everyone to a review guide channel",
                extra={"slack_channel": channel_id, "slack_error": str(exc)},
            )
        try:
            await set_context_bar(
                channel_id,
                repo_context_bar_items(
                    {"owner": start.owner, "name": start.repo},
                    pr_url=self.pull_request.url,
                    dashboard_url=dashboard_thread_url(thread_id) or "",
                ),
            )
        except SlackRequestError:
            logger.warning(
                "Could not set a review guide channel's context bar",
                extra={"slack_channel": channel_id},
                exc_info=True,
            )
        return await self.begin(thread_id)


async def start_review_guide(start: GuideStart) -> StartedGuide:
    launch = await GuideLaunch.check(start)
    return await (launch.code_channel() if start.code_channel else launch.here())


class _SourceMetadata(BaseModel):
    sandbox_id: str | None = None
    proxy_config: JsonValue = Field(default=None, alias=SANDBOX_PROXY_CONFIG_METADATA_KEY)


async def _fork(source_thread_id: str, login: str, *, is_private: bool = False) -> str:
    """A copy of the asking thread that shares its sandbox, so the walkthrough knows its work.

    The copy keeps every checkpoint but none of the source's other metadata: its
    Slack location, title and run bookkeeping belong to the conversation it came
    from. It joins the source's sandbox as a guest, the way other threads borrow one.
    """
    try:
        thread = await THREADS.get(source_thread_id)
        if thread is None:
            raise LookupError("the asking thread is not indexed")
        source = _SourceMetadata.model_validate(thread.metadata)
        host = await sandbox_host_thread_id(source_thread_id)
        copied = await THREADS.copy(thread)
        await THREADS.update(
            copied.thread_id,
            {
                **dict.fromkeys(copied.metadata),
                "visibility": "private" if is_private else "public",
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
    session: ReviewGuideSession, text: str, *, approve_ts: str = ""
) -> None:
    """Start a walkthrough turn no person typed, so it cannot approve the pull request.

    ``approve_ts`` names a message whose "Next" the turn records before the model runs.
    """
    location = session.slack_location
    reader = await User.get(session.user_id)
    pr = session.pull_request
    # Set every run: a thread carries its last run's configurable into the next.
    configurable: dict[str, object] = {
        "thread_id": session.thread_id,
        "repo": {"owner": pr.owner, "name": pr.repo},
        "source": "slack" if location else GUIDE_RUN_SOURCE,
        "review_guide_approve_ts": approve_ts,
    }
    if location is not None:
        configurable["slack_thread"] = location.dump()
    if reader is not None and reader.github_login:
        configurable["github_login"] = reader.github_login
    if session.workspace_slug:
        configurable["workspace"] = session.workspace_slug
    if session.in_code_channel:
        await set_session_status(session.slack_channel_id, "processing")
    await create_durable_run(
        session.thread_id,
        "agent",
        input=build_run_input(
            text,
            {"sender_id": _SENDER_ID, "surface": "automation", "kind": "system"},
            systems=[{"id": _SENDER_ID, "display_name": "Review guide", "platform": "open-swe"}],
        ),
        source=GUIDE_RUN_SOURCE,
        thread_title=None,
        config={"configurable": with_invocation_id(configurable, new_invocation_id())},
        multitask_strategy="enqueue",
    )


class _ChannelState(BaseModel):
    is_archived: bool = False


async def channel_archived(channel_id: str) -> bool:
    channel = await SlackChannel.fetch(channel_id, use_cache=False)
    return channel is not None and _ChannelState.model_validate(channel).is_archived


async def close_guide_for_channel(channel_id: str) -> None:
    """Close the guide bound to a channel Slack just archived, if there is one."""
    session = await ReviewGuideSession.for_code_channel(channel_id)
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
        if session.in_code_channel and await channel_archived(session.slack_channel_id):
            await session.set_closed(True)
            continue
        try:
            await pause(session, event.pull_request.head_sha)
            await refresh_progress(session, await session.reader(), stage="paused")
        except Exception:
            logger.exception(
                "Could not pause a review guide for an updated pull request",
                extra={"agent_thread_id": session.thread_id, "pr_number": number},
            )
