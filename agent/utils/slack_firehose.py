"""Mirror every agent thread into a single Slack "firehose" channel.

The firehose is a read-only duplicate of a thread: the inbound request, the
agent's own prose, and one rolling task card standing in for the tool calls
behind each turn. Nothing here routes back into the agent, and every mirrored
write runs off the run's critical path — a Slack failure can never fail a run.
"""

import asyncio
import contextvars
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langgraph_sdk import get_client

from ..dashboard.team_settings import get_team_firehose_channel_id
from .dashboard_links import dashboard_thread_url
from .slack import (
    post_slack_thread_reply_with_ts,
    post_slack_top_level_message_with_ts,
    update_slack_message,
)

logger = logging.getLogger(__name__)

FIREHOSE_CHANNEL_METADATA_KEY = "firehose_channel_id"
FIREHOSE_TS_METADATA_KEY = "firehose_thread_ts"

MARKDOWN_MAX_CHARS = 11_000
TITLE_MAX_CHARS = 150
ACTIVITY_DETAIL_LINES = 8
_ARG_VALUE_MAX_CHARS = 120

_SOURCE_LABELS = {
    "slack": "Slack",
    "linear": "Linear",
    "github": "GitHub",
    "dashboard": "Web",
    "schedule": "Scheduled",
}

# Tool args worth showing, in the order they should be tried.
_TOOL_ARG_KEYS: dict[str, tuple[str, ...]] = {
    "execute": ("command",),
    "background_execute": ("command",),
    "read_file": ("file_path",),
    "write_file": ("file_path",),
    "edit_file": ("file_path",),
    "delete": ("file_path",),
    "ls": ("path",),
    "glob": ("pattern",),
    "grep": ("pattern",),
    "task": ("description", "subagent_type"),
    "web_search": ("query",),
    "fetch_url": ("url",),
    "http_request": ("url",),
    "search_repo_code": ("query",),
    "read_repo_file": ("path",),
}

# Flipped off the first time Slack rejects a task_card block, so a workspace
# without the agent blocks still gets a readable firehose.
_task_cards_supported = True
_UNSUPPORTED_BLOCK_ERRORS = frozenset(
    {"invalid_blocks", "invalid_blocks_format", "invalid_arguments"}
)


@dataclass
class _FirehoseThread:
    channel_id: str
    thread_ts: str
    title: str = ""
    source: str = ""
    repo: str | None = None
    requester: str | None = None
    thread_url: str | None = None
    seen_message_ids: set[str] = field(default_factory=set)
    activity_ts: str | None = None
    activity_lines: list[str] = field(default_factory=list)
    activity_total: int = 0
    activity_seq: int = 0

    def claim(self, message_id: str | None) -> bool:
        """False when this message was already mirrored (a retried or resumed run)."""
        if not message_id:
            return True
        if message_id in self.seen_message_ids:
            return False
        self.seen_message_ids.add(message_id)
        return True


_threads: dict[str, _FirehoseThread] = {}
_chain: dict[str, asyncio.Task[None]] = {}


def _drop_chain(thread_id: str, task: asyncio.Task[None]) -> None:
    if _chain.get(thread_id) is task:
        _chain.pop(thread_id, None)


def _enqueue(thread_id: str, work: Callable[[], Awaitable[None]]) -> None:
    """Run ``work`` after everything already queued for this thread, never raising.

    Slack renders the firehose in arrival order, so mirrored writes stay
    serialized per thread even though each is fired off the critical path. The
    task runs in a fresh context, not the caller's: an inherited context carries
    LangGraph's stream writer, and anything emitted under it would surface in the
    run's own message stream.
    """
    previous = _chain.get(thread_id)

    async def run() -> None:
        if previous is not None:
            try:
                await previous
            except Exception:
                pass
        try:
            await work()
        except Exception:
            logger.warning("Firehose mirroring failed for %s", thread_id, exc_info=True)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(run(), context=contextvars.Context())
    _chain[thread_id] = task
    task.add_done_callback(lambda done: _drop_chain(thread_id, done))


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def describe_tool_call(tool_call: Mapping[str, Any]) -> str:
    """Render a tool call as one scannable line."""
    name = str(tool_call.get("name") or "tool")
    args = tool_call.get("args")
    if not isinstance(args, Mapping):
        return name
    for key in _TOOL_ARG_KEYS.get(name, ()):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return f"{name}: {_truncate(' '.join(value.split()), _ARG_VALUE_MAX_CHARS)}"
    return name


def _markdown_block(text: str) -> dict[str, Any]:
    return {"type": "markdown", "text": _truncate(text, MARKDOWN_MAX_CHARS)}


def _activity_summary(entry: _FirehoseThread) -> str:
    plural = "" if entry.activity_total == 1 else "s"
    return f"{entry.activity_total} tool call{plural}"


def _activity_lines(entry: _FirehoseThread) -> list[str]:
    shown = entry.activity_lines[-ACTIVITY_DETAIL_LINES:]
    hidden = entry.activity_total - len(shown)
    if hidden > 0:
        return [f"…{hidden} earlier tool calls", *shown]
    return shown


def _task_card_block(entry: _FirehoseThread, *, complete: bool) -> dict[str, Any]:
    return {
        "type": "task_card",
        "task_id": f"{entry.thread_ts}-{entry.activity_seq}",
        "title": _truncate(_activity_summary(entry), TITLE_MAX_CHARS),
        "status": "complete" if complete else "in_progress",
        "details": {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_list",
                    "style": "bullet",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [{"type": "text", "text": line}],
                        }
                        for line in _activity_lines(entry)
                    ],
                }
            ],
        },
    }


def _activity_fallback_block(entry: _FirehoseThread, *, complete: bool) -> dict[str, Any]:
    icon = "✅" if complete else "⏳"
    body = "\n".join(f"- `{line}`" for line in _activity_lines(entry))
    return _markdown_block(f"{icon} **{_activity_summary(entry)}**\n{body}")


def _activity_blocks(entry: _FirehoseThread, *, complete: bool) -> list[dict[str, Any]]:
    if _task_cards_supported:
        return [_task_card_block(entry, complete=complete)]
    return [_activity_fallback_block(entry, complete=complete)]


def _root_blocks(entry: _FirehoseThread) -> list[dict[str, Any]]:
    heading = (
        f"**[{entry.title}]({entry.thread_url})**" if entry.thread_url else f"**{entry.title}**"
    )
    context = [_SOURCE_LABELS.get(entry.source, entry.source or "agent")]
    if entry.repo:
        context.append(entry.repo)
    if entry.requester:
        context.append(entry.requester)
    return [
        _markdown_block(heading),
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "  •  ".join(context)}]},
    ]


def _stored_root(metadata: Mapping[str, Any]) -> tuple[str, str] | None:
    channel_id = metadata.get(FIREHOSE_CHANNEL_METADATA_KEY)
    thread_ts = metadata.get(FIREHOSE_TS_METADATA_KEY)
    if isinstance(channel_id, str) and channel_id and isinstance(thread_ts, str) and thread_ts:
        return channel_id, thread_ts
    return None


async def _thread_metadata(thread_id: str) -> dict[str, Any]:
    try:
        thread = await get_client().threads.get(thread_id)
    except Exception:
        logger.debug("Firehose could not read thread %s", thread_id, exc_info=True)
        return {}
    metadata = thread.get("metadata") if isinstance(thread, Mapping) else None
    return dict(metadata) if isinstance(metadata, Mapping) else {}


async def _reply(
    entry: _FirehoseThread, text: str, blocks: list[dict[str, Any]]
) -> tuple[str | None, str | None]:
    message_ts, error = await post_slack_thread_reply_with_ts(
        entry.channel_id,
        entry.thread_ts,
        text,
        blocks=blocks,
    )
    if message_ts is None:
        logger.warning("Firehose reply failed in %s: %s", entry.channel_id, error)
    return message_ts, error


async def _ensure_root(
    thread_id: str,
    *,
    fallback_title: str,
    source: str,
    repo: str | None,
    requester: str | None,
) -> _FirehoseThread | None:
    channel_id = await get_team_firehose_channel_id()
    if not channel_id:
        return None
    metadata = await _thread_metadata(thread_id)
    stored_title = metadata.get("title")
    title = stored_title if isinstance(stored_title, str) and stored_title else fallback_title

    entry = _threads.get(thread_id)
    if entry is not None:
        if title != entry.title:
            entry.title = title
            await update_slack_message(
                entry.channel_id,
                entry.thread_ts,
                _truncate(title, TITLE_MAX_CHARS),
                unfurl_links=False,
                unfurl_media=False,
                blocks=_root_blocks(entry),
            )
        return entry

    entry = _FirehoseThread(
        channel_id=channel_id,
        thread_ts="",
        title=title,
        source=source,
        repo=repo,
        requester=requester,
        thread_url=dashboard_thread_url(thread_id),
    )
    stored = _stored_root(metadata)
    if stored is not None and stored[0] == channel_id:
        entry.thread_ts = stored[1]
        _threads[thread_id] = entry
        return entry

    message_ts, error = await post_slack_top_level_message_with_ts(
        channel_id,
        _truncate(title, TITLE_MAX_CHARS),
        unfurl_links=False,
        unfurl_media=False,
        blocks=_root_blocks(entry),
    )
    if message_ts is None:
        logger.warning("Firehose root post failed for %s: %s", thread_id, error)
        return None
    entry.thread_ts = message_ts
    _threads[thread_id] = entry
    try:
        await get_client().threads.update(
            thread_id=thread_id,
            metadata={
                FIREHOSE_CHANNEL_METADATA_KEY: channel_id,
                FIREHOSE_TS_METADATA_KEY: message_ts,
            },
        )
    except Exception:
        logger.warning("Firehose root metadata update failed for %s", thread_id, exc_info=True)
    return entry


async def _mirror_prose(entry: _FirehoseThread, text: str) -> None:
    # The agent speaking ends the stretch of work above it, so that card settles
    # rather than sitting at in_progress for the life of the thread.
    await _update_activity(entry, complete=True)
    entry.activity_ts = None
    entry.activity_lines = []
    entry.activity_total = 0
    await _reply(entry, _truncate(text, TITLE_MAX_CHARS), [_markdown_block(text)])


async def _mirror_tool_calls(
    entry: _FirehoseThread, tool_calls: Sequence[Mapping[str, Any]]
) -> None:
    global _task_cards_supported
    entry.activity_lines.extend(describe_tool_call(call) for call in tool_calls)
    entry.activity_total += len(tool_calls)
    if entry.activity_ts is not None:
        await _update_activity(entry, complete=False)
        return
    entry.activity_seq += 1
    entry.activity_ts, error = await _reply(
        entry, _activity_summary(entry), _activity_blocks(entry, complete=False)
    )
    if entry.activity_ts is None and _task_cards_supported and error in _UNSUPPORTED_BLOCK_ERRORS:
        _task_cards_supported = False
        entry.activity_ts, _ = await _reply(
            entry, _activity_summary(entry), _activity_blocks(entry, complete=False)
        )


async def _update_activity(entry: _FirehoseThread, *, complete: bool) -> None:
    if entry.activity_ts is None:
        return
    ok, error = await update_slack_message(
        entry.channel_id,
        entry.activity_ts,
        _activity_summary(entry),
        unfurl_links=False,
        unfurl_media=False,
        blocks=_activity_blocks(entry, complete=complete),
    )
    if not ok:
        logger.debug("Firehose activity update failed in %s: %s", entry.channel_id, error)


def record_inbound(
    thread_id: str,
    *,
    text: str,
    message_id: str | None,
    source: str,
    repo: str | None = None,
    requester: str | None = None,
) -> None:
    """Open (or reuse) this thread's firehose thread and mirror the inbound request."""

    async def work() -> None:
        entry = await _ensure_root(
            thread_id,
            fallback_title=_truncate(text, TITLE_MAX_CHARS) or "Agent thread",
            source=source,
            repo=repo,
            requester=requester,
        )
        if entry is None or not text.strip() or not entry.claim(message_id):
            return
        who = requester or _SOURCE_LABELS.get(source, source or "agent")
        await _mirror_prose(entry, f"**{who}**\n\n{text}")

    _enqueue(thread_id, work)


def record_turn(
    thread_id: str,
    *,
    text: str,
    tool_calls: Sequence[Mapping[str, Any]],
    message_id: str | None,
) -> None:
    """Mirror one model turn: its prose, then the tool calls it kicked off."""

    async def work() -> None:
        entry = _threads.get(thread_id)
        if entry is None or not entry.claim(message_id):
            return
        if text.strip():
            await _mirror_prose(entry, text)
        if tool_calls:
            await _mirror_tool_calls(entry, tool_calls)

    _enqueue(thread_id, work)


def record_run_end(thread_id: str) -> None:
    """Settle the rolling activity card once the run stops."""

    async def work() -> None:
        # Dropped rather than settled in place: the root message ts lives in thread
        # metadata, so the next run rehydrates from there and the server does not
        # accumulate an entry per thread it has ever seen.
        entry = _threads.pop(thread_id, None)
        if entry is None or entry.activity_ts is None:
            return
        await _update_activity(entry, complete=True)

    _enqueue(thread_id, work)
