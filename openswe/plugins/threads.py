"""Agent threads as a plugin with ``threads:read`` reads them, for the person using it.

A plugin reads a thread only when that person could: the dashboard's
:func:`thread_is_readable`, plus incident threads (see :meth:`PluginThreads.may_read`).
Threads that stay out of every thread list, such as plugin and review chats,
stay out of a plugin's list too, though one its person may open reads like any.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, JsonValue

from openswe.slack.allowed_bots import allowed_slack_bot_directory
from openswe.source_context import SourceContext
from openswe.threads import index
from openswe.threads.index import IndexedThread
from openswe.threads.plan_store import get_plan_content
from openswe.threads.store import THREADS
from openswe.threads.summary import (
    DASHBOARD_SOURCE,
    SLACK_BOT_TRIGGER_KIND,
    TRIGGERING_BOT_KEY,
    run_status_to_agent_status,
    thread_is_bot_triggered,
    thread_is_private,
    thread_is_readable,
    thread_is_unlisted,
    thread_source,
)
from openswe.transcript.snapshot import MAX_TURN_PAGE_SIZE, MessageView, load_snapshot
from openswe.utils.dashboard_links import dashboard_thread_url

INCIDENT_SOURCE = "incidents_agent"
"""``openswe.incidents.channels.INCIDENT_SOURCE``, kept here so plugins don't import Slack."""
INCIDENT_CHANNEL_KEY = "incident_channel"
"""Thread metadata naming the channel an incident investigation started in: ``{id, name}``."""

ThreadSource = Literal[
    "dashboard",
    "web",
    "github",
    "slack",
    "linear",
    "schedule",
    "api",
    "claude_code",
    "incidents_agent",
]
"""Sources a plugin may list threads of: those the dashboard lists, and incidents."""

ANY_BOT = "*"
"""The ``bot`` filter that keeps threads any allowlisted Slack bot started."""

MAX_THREAD_PAGE = 50
MAX_MESSAGES = 200
MAX_MESSAGE_CHARS = 20_000


class ThreadNotFound(LookupError):
    def __init__(self) -> None:
        super().__init__("thread not found")


class BotModel(BaseModel):
    """An allowlisted Slack bot, as any signed-in person may see it."""

    key: str
    """``<team_id>:<bot_id>``, what the ``bot`` filter takes."""
    name: str | None
    """``None`` once the bot is no longer on the allowlist."""


class SlackThreadModel(BaseModel):
    channel_id: str
    thread_ts: str
    channel_name: str | None
    """The channel's name when the thread began, for incident threads."""
    permalink: str | None

    @classmethod
    def of(cls, metadata: Mapping[str, object]) -> Self | None:
        """The Slack thread the agent thread answers in, if it began in Slack."""
        ref = SourceContext.from_metadata(metadata).slack_thread
        if ref is None or ref.location is None:
            return None
        channel_id, thread_ts = ref.location
        name = ref.channel_context.label if ref.channel_context else ""
        incident = metadata.get(INCIDENT_CHANNEL_KEY)
        if not name and isinstance(incident, Mapping) and incident.get("id") == channel_id:
            found = incident.get("name")
            name = found if isinstance(found, str) else ""
        return cls(
            channel_id=channel_id,
            thread_ts=thread_ts,
            channel_name=name or None,
            permalink=ref.permalink or None,
        )


class ThreadSummaryModel(BaseModel):
    thread_id: str
    title: str | None
    source: str
    status: str
    """``running``, ``idle``, ``error``, ``interrupted`` and the like, as the dashboard shows it."""
    visibility: Literal["public", "private"]
    created_at: datetime
    updated_at: datetime
    url: str | None
    """The thread in the dashboard; ``None`` for one it won't open, such as an incident thread."""
    slack: SlackThreadModel | None
    has_artifact: bool
    """Whether the thread published an artifact, read from ``/artifact``."""
    bot: BotModel | None
    """The allowlisted Slack bot that started the thread, if one did."""

    @classmethod
    def of(
        cls, thread: IndexedThread, *, opens_in_dashboard: bool, bot_names: Mapping[str, str]
    ) -> Self:
        metadata = thread.metadata
        bot_key = metadata.get(TRIGGERING_BOT_KEY)
        bot = (
            BotModel(key=bot_key, name=bot_names.get(bot_key))
            if thread_is_bot_triggered(metadata) and isinstance(bot_key, str) and bot_key
            else None
        )
        title = metadata.get("title")
        return cls(
            thread_id=thread.thread_id,
            title=title if isinstance(title, str) and title else None,
            source=thread_source(metadata),
            status=cls._status(thread),
            visibility="private" if thread_is_private(metadata) else "public",
            created_at=thread.created_at,
            updated_at=thread.updated_at,
            url=dashboard_thread_url(thread.thread_id) if opens_in_dashboard else None,
            slack=SlackThreadModel.of(metadata),
            has_artifact=isinstance(metadata.get("plan_status"), str),
            bot=bot,
        )

    @staticmethod
    def _status(thread: IndexedThread) -> str:
        """The thread's status as the dashboard shows it. The index is reconciled when a run
        settles and the run metadata isn't, so a ``pending``/``running`` there that the index
        no longer backs is stale, and an index ``error`` without run metadata is an error."""
        latest = thread.metadata.get("latest_run_status")
        latest = latest if isinstance(latest, str) else None
        if thread.status != "busy" and latest in ("pending", "running"):
            latest = None
        if thread.status == "error" and latest is None:
            return "error"
        return run_status_to_agent_status(thread.status, latest)


class ThreadPageModel(BaseModel):
    threads: list[ThreadSummaryModel]
    next_offset: int | None
    """Where the next page starts; ``None`` at the end. A page may hold fewer threads
    than asked for, since threads the person can't read are left out."""


class ThreadMessageModel(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    text: str
    truncated: bool
    sender_id: str | None
    """Who sent a user message: a GitHub login, ``slack:…``, ``system:…`` and the like."""
    sender_name: str | None
    timestamp: datetime

    @classmethod
    def of(cls, view: MessageView) -> Self:
        sender = view.sender or {}
        login, name = sender.get("login"), sender.get("display_name")
        return cls(
            id=view.message_id,
            role="user" if view.role == "human" else "assistant",
            text=view.text[:MAX_MESSAGE_CHARS],
            truncated=len(view.text) > MAX_MESSAGE_CHARS,
            sender_id=login if isinstance(login, str) and view.role == "human" else None,
            sender_name=name if isinstance(name, str) and view.role == "human" else None,
            timestamp=view.created_at,
        )


class ThreadMessagesModel(BaseModel):
    messages: list[ThreadMessageModel]
    """The newest messages people and the agent wrote, oldest first."""
    omitted: int
    """Earlier messages left out to keep within the limit."""

    @classmethod
    def of(cls, views: Sequence[MessageView], *, limit: int) -> Self:
        """The newest ``limit`` of ``views`` with text, leaving out subagents' messages."""
        readable = [view for view in views if not view.namespace and view.text.strip()]
        kept = readable[-limit:]
        return cls(
            messages=[ThreadMessageModel.of(view) for view in kept],
            omitted=len(readable) - len(kept),
        )


class ThreadArtifactModel(BaseModel):
    status: str | None
    format: Literal["html", "markdown"]
    content: str


@dataclass(frozen=True, slots=True)
class PluginThreads:
    """Agent threads, read as ``login`` (the dashboard session's subject) reads them."""

    login: str
    email: str | None

    def may_read(self, metadata: Mapping[str, object]) -> bool:
        """Whether this person's plugin may read a thread with ``metadata``.

        What the dashboard lets them read, and also public, system-owned incident
        threads. The dashboard reads no incident thread, for anyone: their source is
        not one it surfaces, and its lists and admin cancel skip them so they stay out
        of people's sidebars. That is about clutter, not secrecy. Robin starts them only
        in public, internal, unshared Slack channels (``incidents.service.channel_allowed``),
        every member of the workspace reads them there, and the ``/incidents`` page they
        used to appear on showed them to everyone signed in. A plugin reads one only once
        the person approved ``threads:read``; a private or person-owned thread that
        happens to carry the incident source is held to the dashboard's rules.
        """
        if thread_is_readable(metadata, self.login, self.email):
            return True
        return (
            thread_source(metadata) == INCIDENT_SOURCE
            and not thread_is_private(metadata)
            and metadata.get("owner_type") == "system"
        )

    async def bots(self) -> list[BotModel]:
        """The allowlisted Slack bots, whose threads ``page`` lists with ``bot``."""
        return [
            BotModel(key=entry.key, name=entry.name)
            for entry in await allowed_slack_bot_directory()
        ]

    async def page(
        self, source: ThreadSource, *, bot: str | None, limit: int, offset: int
    ) -> ThreadPageModel:
        """Threads from ``source`` the person may read, most recently updated first.

        ``bot`` keeps those one allowlisted Slack bot started, or any bot with ``*``.
        """
        # Like the dashboard's search, a thread with no source is a dashboard thread, so
        # that source is matched after the search rather than in it.
        metadata: dict[str, JsonValue] = {} if source == DASHBOARD_SOURCE else {"source": source}
        if bot is not None:
            metadata["trigger_kind"] = SLACK_BOT_TRIGGER_KIND
            if bot != ANY_BOT:
                metadata[TRIGGERING_BOT_KEY] = bot
        found = await THREADS.search(
            index.ThreadSearch(metadata=metadata, sort_by="updated_at", limit=limit, offset=offset)
        )
        names = await self._bot_names(found)
        return ThreadPageModel(
            threads=[
                self._summary(thread, names)
                for thread in found
                if thread_source(thread.metadata) == source
                and not thread_is_unlisted(thread.metadata)
                and self.may_read(thread.metadata)
            ],
            next_offset=offset + len(found) if len(found) == limit else None,
        )

    async def get(self, thread_id: str) -> IndexedThread:
        """The thread, or :class:`ThreadNotFound` when it is missing or the person can't read it."""
        thread = await THREADS.get(thread_id)
        if thread is None or not self.may_read(thread.metadata):
            raise ThreadNotFound
        return thread

    async def summary(self, thread_id: str) -> ThreadSummaryModel:
        thread = await self.get(thread_id)
        return self._summary(thread, await self._bot_names([thread]))

    def _summary(self, thread: IndexedThread, bot_names: Mapping[str, str]) -> ThreadSummaryModel:
        opens = thread_is_readable(thread.metadata, self.login, self.email)
        return ThreadSummaryModel.of(thread, opens_in_dashboard=opens, bot_names=bot_names)

    @staticmethod
    async def _bot_names(threads: Sequence[IndexedThread]) -> dict[str, str]:
        """Allowlisted bots' names by key, read only when one of ``threads`` is a bot's."""
        if not any(thread_is_bot_triggered(thread.metadata) for thread in threads):
            return {}
        return {entry.key: entry.name for entry in await allowed_slack_bot_directory()}

    async def messages(self, thread_id: str, *, limit: int) -> ThreadMessagesModel | None:
        """The newest ``limit`` messages of the thread's recent turns, from its transcript.

        ``None`` for a thread with no transcript: one from before the event log keeps its
        messages only in the legacy Agent Server, which new code doesn't read.
        """
        await self.get(thread_id)
        snapshot = await load_snapshot(thread_id, limit=MAX_TURN_PAGE_SIZE)
        return ThreadMessagesModel.of(snapshot.messages, limit=limit) if snapshot else None

    async def artifact(self, thread_id: str) -> ThreadArtifactModel | None:
        """What the thread published with ``publish_artifact`` or as its plan, if anything."""
        await self.get(thread_id)
        content = await get_plan_content(thread_id, raise_on_error=True) or {}
        status = content.get("status")
        status = status if isinstance(status, str) else None
        if isinstance(html := content.get("html"), str) and html:
            return ThreadArtifactModel(status=status, format="html", content=html)
        if isinstance(markdown := content.get("markdown"), str) and markdown:
            return ThreadArtifactModel(status=status, format="markdown", content=markdown)
        return None
