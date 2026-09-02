"""Project a run into a Slack session as it happens.

The web dashboard needs nothing to show a run: it reads the thread. A Slack
session has to be told, so this consumes a run's event stream and mirrors it
into one streaming Slack message per run — the agent's own words as streamed
text, its tool calls as task cards interleaved with them.

Only the top-level agent's text is mirrored. Subagents run in their own
namespaces and the dashboard nests their output; a Slack channel has nowhere to
nest it, so it stays out of the transcript and only its task cards show.
"""

import asyncio
import hashlib
import logging
from contextlib import suppress
from dataclasses import dataclass
from pathlib import PurePath
from time import monotonic
from typing import Any, Literal

from langgraph_sdk.client import LangGraphClient

from agent.source_context import SlackThreadRef
from agent.surfaces.slack import SlackChannelSurface
from agent.utils.slack import (
    SlackStreamError,
    append_slack_stream,
    lookup_slack_run_message_mapping,
    start_slack_stream,
    stop_slack_stream,
    store_slack_message_run_mapping,
    store_slack_run_mapping,
)

logger = logging.getLogger(__name__)

#: Live projections, kept only so the event loop does not collect them mid-run.
_projections: set[asyncio.Task[None]] = set()

StepStatus = Literal["in_progress", "complete", "error"]
_FLUSH_INTERVAL_SECONDS = 1.0
_DEFAULT_RETRY_SECONDS = 30.0
_MAX_RETRY_SECONDS = 300.0
# Slack caps `markdown_text` at 12k characters. Roll over to a new streaming
# message before a long run's transcript reaches the limit and the append that
# would exceed it is rejected outright.
_STREAM_TEXT_LIMIT = 9_000


@dataclass
class Step:
    task_id: str
    title: str
    status: StepStatus
    details: str = ""
    output: str = ""

    def chunk(self) -> dict[str, Any]:
        chunk: dict[str, Any] = {
            "type": "task_update",
            "id": self.task_id,
            "title": self.title[:256],
            "status": self.status,
        }
        if self.details:
            chunk["details"] = self.details[:256]
        if self.output:
            chunk["output"] = self.output[:256]
        return chunk


def _text_arg(value: Any, key: str) -> str:
    if not isinstance(value, dict):
        return ""
    item = value.get(key)
    return item if isinstance(item, str) else ""


def _basename(value: str) -> str:
    return PurePath(value).name if value else "file"


def _tool_step(name: str, tool_input: Any) -> tuple[str, str]:
    if name in {"read_file", "write_file", "edit_file", "delete"}:
        action = {
            "read_file": "Reading",
            "write_file": "Writing",
            "edit_file": "Editing",
            "delete": "Removing",
        }[name]
        return f"{action} {_basename(_text_arg(tool_input, 'file_path'))}", "Repository file"
    if name in {"glob", "grep"}:
        return "Searching repository files", "Search details hidden"
    if name in {"web_search", "fetch_url"}:
        return "Searching external documentation", "External source lookup"
    if name in {"execute", "background_execute"}:
        return "Running a development command", _text_arg(tool_input, "command")
    if name == "task":
        agent = _text_arg(tool_input, "subagent_type").replace("-", " ")
        return f"Delegating to {agent or 'a specialist'}", "Specialized agent task"
    labels = {
        "ls": ("Inspecting repository files", "Repository directory"),
        "open_pull_request": ("Opening pull request", "GitHub operation"),
        "request_pr_review": ("Starting pull request review", "GitHub operation"),
        "save_plan": ("Publishing implementation plan", "Plan artifact"),
        "analyzePlan": ("Checking implementation security", "Security analysis"),
    }
    return labels.get(name, (f"Using {name.replace('_', ' ')}", "Tool call"))


def _step_id(run_id: str, namespace: tuple[str, ...], call_id: str) -> str:
    value = "\0".join((run_id, *namespace, call_id))
    return f"step-{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def _part_value(part: Any, key: str) -> Any:
    return part.get(key) if isinstance(part, dict) else getattr(part, key, None)


def _event_data(part: Any) -> tuple[str, tuple[str, ...], dict[str, Any]] | None:
    """Split a stream part into ``(method, namespace, data)``.

    The protocol names an event ``<method>|<namespace...>`` and may deliver the
    payload either bare or wrapped in a ``params`` envelope.
    """
    event = _part_value(part, "event")
    raw = _part_value(part, "data")
    if not isinstance(event, str) or not isinstance(raw, dict):
        return None
    method, *segments = event.split("|")
    namespace = tuple(segment for segment in segments if segment)
    params = raw.get("params")
    if isinstance(params, dict):
        nested_namespace = params.get("namespace")
        if isinstance(nested_namespace, list):
            namespace = tuple(str(value) for value in nested_namespace)
        raw = params.get("data")
    return (method, namespace, raw) if isinstance(raw, dict) else None


def _text_delta(data: dict[str, Any]) -> str:
    """The text a ``content-block-delta`` adds, ignoring reasoning and tool blocks."""
    content = data.get("content")
    if not isinstance(content, dict) or content.get("type") != "text":
        return ""
    text = content.get("text")
    return text if isinstance(text, str) else ""


class SlackTranscript:
    def __init__(
        self,
        *,
        client: LangGraphClient,
        thread_id: str,
        run_id: str,
        channel_id: str,
        thread_ts: str,
        recipient_user_id: str,
        recipient_team_id: str,
        mapping_thread_ts: str,
        original_message_ts: str,
    ) -> None:
        self.client = client
        self.thread_id = thread_id
        self.run_id = run_id
        self.channel_id = channel_id
        self.thread_ts = thread_ts
        self.recipient_user_id = recipient_user_id
        self.recipient_team_id = recipient_team_id
        self.mapping_thread_ts = mapping_thread_ts
        self.original_message_ts = original_message_ts
        self.message_ts: str | None = None
        self.steps: dict[tuple[tuple[str, ...], str], Step] = {}
        # Chunks in the order they happened, so text and task cards interleave the
        # way the run did. A step that updates replaces its earlier pending chunk
        # in place: Slack identifies a task card by id, not by position.
        self.pending: list[dict[str, Any]] = []
        self.pending_steps: dict[str, int] = {}
        self.roles: dict[tuple[str, ...], str] = {}
        self.streamed_chars = 0
        self.last_flush = monotonic()
        self.retry_at = 0.0
        self.disabled = False

    def _queue_step(self, step: Step) -> None:
        index = self.pending_steps.get(step.task_id)
        if index is None:
            self.pending_steps[step.task_id] = len(self.pending)
            self.pending.append(step.chunk())
        else:
            self.pending[index] = step.chunk()

    def _queue_text(self, text: str) -> None:
        if not text:
            return
        if self.pending and self.pending[-1].get("type") == "markdown_text":
            self.pending[-1]["text"] += text
        else:
            self.pending.append({"type": "markdown_text", "text": text})

    async def start(self) -> bool:
        initial = Step(
            _step_id(self.run_id, (), "startup"), "Preparing the agent workspace", "in_progress"
        )
        try:
            self.message_ts = await start_slack_stream(
                self.channel_id,
                self.thread_ts,
                [initial.chunk()],
                recipient_user_id=self.recipient_user_id,
                recipient_team_id=self.recipient_team_id,
                task_display_mode="timeline",
            )
        except SlackStreamError as exc:
            logger.info("Slack run projection unavailable for run %s: %s", self.run_id, exc.code)
            return False
        self.steps[((), "startup")] = initial
        await store_slack_run_mapping(
            self.client,
            self.channel_id,
            self.mapping_thread_ts,
            self.run_id,
            message_ts=self.original_message_ts,
            triggering_user_id=self.recipient_user_id,
            agent_thread_id=self.thread_id,
            thinking_message_ts=self.message_ts,
        )
        await self._map_message(self.message_ts)
        return True

    async def _map_message(self, message_ts: str) -> None:
        """Point this run's mappings at the message it is streaming into.

        The message mapping is what lets a reaction on the transcript resolve to
        the run behind it; `thinking_message_ts` is what closing out stops.
        """
        await store_slack_run_mapping(
            self.client,
            self.channel_id,
            self.mapping_thread_ts,
            self.run_id,
            triggering_user_id=self.recipient_user_id,
            agent_thread_id=self.thread_id,
            thinking_message_ts=message_ts,
        )
        await store_slack_message_run_mapping(
            self.client,
            self.channel_id,
            self.mapping_thread_ts,
            message_ts,
            run_id=self.run_id,
            triggering_user_id=self.recipient_user_id or None,
        )

    def consume(self, part: Any) -> None:
        parsed = _event_data(part)
        if parsed is None:
            return
        method, namespace, data = parsed
        if method == "messages":
            self._consume_message(namespace, data)
            return
        if method != "tools":
            return
        event = data.get("event")
        call_id = data.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            return
        key = (namespace, call_id)
        if event == "tool-started":
            name = data.get("tool_name")
            if not isinstance(name, str):
                return
            startup = self.steps.get(((), "startup"))
            if startup and startup.status == "in_progress":
                startup.status = "complete"
                self._queue_step(startup)
            title, details = _tool_step(name, data.get("input"))
            step = Step(
                _step_id(self.run_id, namespace, call_id),
                title,
                "in_progress",
                details,
            )
            self.steps[key] = step
            self._queue_step(step)
        elif event in {"tool-finished", "tool-error"}:
            step = self.steps.get(key)
            if step is None:
                step = Step(_step_id(self.run_id, namespace, call_id), "Agent step", "complete")
                self.steps[key] = step
            step.status = "error" if event == "tool-error" else "complete"
            step.output = "Failed" if event == "tool-error" else "Completed"
            self._queue_step(step)

    def _consume_message(self, namespace: tuple[str, ...], data: dict[str, Any]) -> None:
        event = data.get("event")
        if event == "message-start":
            role = data.get("role")
            self.roles[namespace] = role if isinstance(role, str) else ""
            return
        if event == "message-finish":
            self.roles.pop(namespace, None)
            return
        # A subagent's narration belongs to its own namespace, and only the
        # top-level agent is talking to the channel.
        if namespace or self.roles.get(namespace) != "ai":
            return
        if event in {"content-block-delta", "content-block-start"}:
            self._queue_text(_text_delta(data))

    async def flush(self, *, force: bool = False) -> None:
        if self.disabled or not self.message_ts or not self.pending:
            return
        now = monotonic()
        if now < self.retry_at or (not force and now - self.last_flush < _FLUSH_INTERVAL_SECONDS):
            return
        chunks = list(self.pending)
        text_chars = sum(
            len(chunk.get("text", "")) for chunk in chunks if chunk.get("type") == "markdown_text"
        )
        if text_chars and self.streamed_chars + text_chars > _STREAM_TEXT_LIMIT:
            if not await self._roll_over():
                return
        try:
            await append_slack_stream(self.channel_id, self.message_ts, chunks)
        except SlackStreamError as exc:
            if exc.code == "rate_limited":
                delay = exc.retry_after if exc.retry_after is not None else _DEFAULT_RETRY_SECONDS
                self.retry_at = monotonic() + min(max(delay, 1.0), _MAX_RETRY_SECONDS)
            else:
                logger.warning("Disabling Slack run projection for %s: %s", self.run_id, exc.code)
                self.disabled = True
            return
        self.pending.clear()
        self.pending_steps.clear()
        self.streamed_chars += text_chars
        self.last_flush = monotonic()
        self.retry_at = 0.0

    async def _roll_over(self) -> bool:
        """Continue a long transcript in a second streaming message."""
        current = self.message_ts
        try:
            if current:
                await stop_slack_stream(self.channel_id, current, session_status="processing")
            self.message_ts = await start_slack_stream(
                self.channel_id,
                self.thread_ts,
                [],
                recipient_user_id=self.recipient_user_id,
                recipient_team_id=self.recipient_team_id,
                task_display_mode="timeline",
            )
        except SlackStreamError as exc:
            logger.warning("Could not continue the transcript for %s: %s", self.run_id, exc.code)
            self.disabled = True
            return False
        self.streamed_chars = 0
        await self._map_message(self.message_ts)
        return True

    async def stop(self, status: str) -> None:
        for step in self.steps.values():
            if step.status == "in_progress":
                step.status = "complete" if status == "success" else "error"
                step.output = "Completed" if status == "success" else "Interrupted"
                self._queue_step(step)
        if self.message_ts:
            try:
                await stop_slack_stream(self.channel_id, self.message_ts, list(self.pending))
            except SlackStreamError as exc:
                logger.warning(
                    "Could not stop the Slack transcript for run %s: %s", self.run_id, exc.code
                )
            else:
                self.pending.clear()
                self.pending_steps.clear()


async def project_run_into_slack(
    *,
    client: LangGraphClient,
    thread_id: str,
    run_id: str,
    channel_id: str,
    thread_ts: str,
    mapping_thread_ts: str,
    original_message_ts: str,
    recipient_user_id: str = "",
    recipient_team_id: str = "",
) -> None:
    """Mirror one run — its words and its tool calls — into a Slack session."""
    stream = SlackTranscript(
        client=client,
        thread_id=thread_id,
        run_id=run_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        recipient_user_id=recipient_user_id,
        recipient_team_id=recipient_team_id,
        mapping_thread_ts=mapping_thread_ts,
        original_message_ts=original_message_ts,
    )
    if not await stream.start():
        return
    status = "error"
    try:
        async for part in client.runs.join_stream(thread_id, run_id):
            stream.consume(part)
            await stream.flush()
        run = await client.runs.get(thread_id, run_id)
        run_status = run.get("status") if isinstance(run, dict) else None
        status = "success" if run_status == "success" else str(run_status or "error")
    except asyncio.CancelledError:
        status = "interrupted"
        raise
    except Exception:
        logger.warning("Slack run projection failed for run %s", run_id, exc_info=True)
    finally:
        try:
            await asyncio.shield(stream.stop(status))
        except Exception:
            logger.warning("Slack run projection cleanup failed for %s", run_id, exc_info=True)


def start_projection(
    client: LangGraphClient,
    *,
    thread_id: str,
    run_id: str,
    surface: SlackChannelSurface,
    location: SlackThreadRef,
) -> None:
    """Project a run in the background, for as long as this process lives.

    Nothing waits on the task: the run outlives the request that dispatched it.
    A projection lost to a restart leaves a streaming message behind, which the
    run's completion closes out.
    """
    from agent.dashboard.slack_oauth import SLACK_TEAM_ID

    task = asyncio.get_running_loop().create_task(
        project_run_into_slack(
            client=client,
            thread_id=thread_id,
            run_id=run_id,
            channel_id=surface.channel_id,
            thread_ts=surface.reply_target(),
            mapping_thread_ts=location.thread_ts,
            original_message_ts=location.triggering_event_ts,
            recipient_user_id=location.triggering_user_id,
            recipient_team_id=location.team_id or SLACK_TEAM_ID,
        )
    )
    _projections.add(task)
    task.add_done_callback(_projections.discard)


async def close_projection(client: LangGraphClient, *, channel_id: str, run_id: str) -> None:
    """Close out a run's streaming message, whatever happened to its projection.

    Stopping a stream that has already stopped is not an error, so this is safe
    to call after a projection ended on its own.
    """
    mapping = await lookup_slack_run_message_mapping(client, channel_id, run_id)
    message_ts = (mapping or {}).get("thinking_message_ts")
    if not isinstance(message_ts, str) or not message_ts:
        return
    with suppress(SlackStreamError):
        await stop_slack_stream(channel_id, message_ts)
