"""Stream sanitized LangGraph tool progress into Slack Thinking Steps."""

import asyncio
import hashlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePath
from time import monotonic
from typing import Any, Literal

from langgraph_sdk.client import LangGraphClient

from agent.slack.client import (
    SlackStreamError,
    append_slack_stream,
    set_slack_thread_status,
    start_slack_stream,
    stop_slack_stream,
    store_slack_run_mapping,
)
from agent.source_context import SourceContext
from agent.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY
from agent.utils.json_types import thread_metadata
from agent.utils.streaming import TERMINAL_LIFECYCLE_EVENTS, root_lifecycle

logger = logging.getLogger(__name__)

StepStatus = Literal["in_progress", "complete", "error"]
_FLUSH_INTERVAL_SECONDS = 1.0
_DEFAULT_RETRY_SECONDS = 30.0
_MAX_RETRY_SECONDS = 300.0
_THINKING_STATUS = "Thinking..."
_STATUS_REFRESH_SECONDS = 90.0
_STATUS_ANCHOR_NAMESPACE = "slack_session_status_anchor"


@dataclass
class Step:
    task_id: str
    title: str
    status: StepStatus
    details: str = ""
    output: str = ""
    failed: bool = False

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


def _event_data(event: Mapping[str, Any]) -> tuple[tuple[str, ...], Mapping[str, Any]] | None:
    if event.get("method") != "tools":
        return None
    params = event.get("params")
    if not isinstance(params, dict):
        return None
    namespace = params.get("namespace")
    data = params.get("data")
    if not isinstance(namespace, list) or not isinstance(data, dict):
        return None
    return tuple(str(segment) for segment in namespace), data


class SlackThinkingStream:
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
        self.pending: dict[str, Step] = {}
        self.last_flush = monotonic()
        self.retry_at = 0.0
        self.disabled = False

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
            )
        except SlackStreamError as exc:
            logger.info("Slack Thinking Steps unavailable for run %s: %s", self.run_id, exc.code)
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
        return True

    def consume(self, stream_event: Mapping[str, Any]) -> None:
        parsed = _event_data(stream_event)
        if parsed is None:
            return
        namespace, data = parsed
        tool_event = data.get("event")
        call_id = data.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            return
        key = (namespace, call_id)
        if tool_event == "tool-started":
            name = data.get("tool_name")
            if not isinstance(name, str):
                return
            startup = self.steps.get(((), "startup"))
            if startup and startup.status == "in_progress":
                startup.status = "complete"
                self.pending[startup.task_id] = startup
            title, details = _tool_step(name, data.get("input"))
            step = Step(
                _step_id(self.run_id, namespace, call_id),
                title,
                "in_progress",
                details,
            )
            self.steps[key] = step
            self.pending[step.task_id] = step
        elif tool_event in {"tool-finished", "tool-error"}:
            step = self.steps.get(key)
            if step is None:
                step = Step(_step_id(self.run_id, namespace, call_id), "Agent step", "complete")
                self.steps[key] = step
            step.failed = tool_event == "tool-error"
            step.status = "complete"
            step.output = "Failed" if step.failed else "Completed"
            self.pending[step.task_id] = step

    async def flush(self, *, force: bool = False) -> None:
        if self.disabled or not self.message_ts or not self.pending:
            return
        now = monotonic()
        if now < self.retry_at or (not force and now - self.last_flush < _FLUSH_INTERVAL_SECONDS):
            return
        chunks = [step.chunk() for step in self.pending.values()]
        try:
            await append_slack_stream(self.channel_id, self.message_ts, chunks)
        except SlackStreamError as exc:
            if exc.code == "rate_limited":
                delay = exc.retry_after if exc.retry_after is not None else _DEFAULT_RETRY_SECONDS
                self.retry_at = monotonic() + min(max(delay, 1.0), _MAX_RETRY_SECONDS)
            else:
                logger.warning(
                    "Disabling Slack Thinking Steps for run %s: %s", self.run_id, exc.code
                )
                self.disabled = True
            return
        self.pending.clear()
        self.last_flush = monotonic()
        self.retry_at = 0.0

    async def stop(self, status: str) -> None:
        for step in self.steps.values():
            if step.failed:
                step.status = "complete" if status == "success" else "error"
                self.pending[step.task_id] = step
            elif step.status == "in_progress":
                step.status = "complete" if status == "success" else "error"
                step.output = "Completed" if status == "success" else "Interrupted"
                self.pending[step.task_id] = step
        if self.message_ts:
            chunks = [step.chunk() for step in self.pending.values()]
            try:
                await stop_slack_stream(self.channel_id, self.message_ts, chunks)
            except SlackStreamError as exc:
                logger.warning(
                    "Could not stop Slack Thinking Steps for run %s: %s", self.run_id, exc.code
                )
            else:
                self.pending.clear()


async def stream_slack_thinking_steps(
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
    """Mirror one run's structured tool lifecycle into a Slack timeline."""
    stream = SlackThinkingStream(
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
        active = False
        async with client.threads.stream(thread_id, assistant_id="agent") as thread_stream:
            async for event in thread_stream.subscribe(["lifecycle", "tools"]):
                lifecycle = root_lifecycle(event)
                if lifecycle is not None and lifecycle[0] == run_id:
                    if lifecycle[1] == "running":
                        active = True
                    elif lifecycle[1] in TERMINAL_LIFECYCLE_EVENTS:
                        status = "success" if lifecycle[1] == "completed" else lifecycle[1]
                        break
                if active:
                    stream.consume(event)
                    await stream.flush()
    except asyncio.CancelledError:
        status = "interrupted"
        raise
    except Exception:
        logger.warning("Slack Thinking Steps observer failed for run %s", run_id, exc_info=True)
    finally:
        try:
            await asyncio.shield(stream.stop(status))
        except Exception:
            logger.warning("Slack Thinking Steps cleanup failed for run %s", run_id, exc_info=True)


async def restore_slack_thinking_status(channel_id: str, thread_ts: str) -> bool:
    """Restore the status Slack clears when the assistant posts a reply."""
    return await set_slack_thread_status(channel_id, thread_ts, _THINKING_STATUS)


async def _claim_status_anchor(
    client: LangGraphClient, channel_id: str, session_ts: str, message_ts: str
) -> str:
    """Take ownership of a session's one status, returning whoever held it before."""
    namespace = (_STATUS_ANCHOR_NAMESPACE, channel_id)
    previous = ""
    try:
        item = await client.store.get_item(namespace, session_ts)
        value = item.get("value") if isinstance(item, Mapping) else None
        held = value.get("message_ts") if isinstance(value, Mapping) else None
        previous = held if isinstance(held, str) else ""
        await client.store.put_item(namespace, session_ts, {"message_ts": message_ts})
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not claim the Slack session status anchor",
            extra={"slack_channel": channel_id},
            exc_info=True,
        )
    return previous


async def _release_status_anchor(
    client: LangGraphClient, channel_id: str, session_ts: str, message_ts: str
) -> bool:
    """Give up the status only while this run still holds it."""
    namespace = (_STATUS_ANCHOR_NAMESPACE, channel_id)
    try:
        item = await client.store.get_item(namespace, session_ts)
        value = item.get("value") if isinstance(item, Mapping) else None
        held = value.get("message_ts") if isinstance(value, Mapping) else None
        if held != message_ts:
            return False
        await client.store.delete_item(namespace, session_ts)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not release the Slack session status anchor",
            extra={"slack_channel": channel_id},
            exc_info=True,
        )
    return True


async def restore_slack_session_status(
    client: LangGraphClient, channel_id: str, session_ts: str
) -> None:
    """Put a session's status back on whichever message currently holds it."""
    namespace = (_STATUS_ANCHOR_NAMESPACE, channel_id)
    try:
        item = await client.store.get_item(namespace, session_ts)
    except Exception:  # noqa: BLE001
        logger.debug("Could not read the Slack session status anchor", exc_info=True)
        return
    value = item.get("value") if isinstance(item, Mapping) else None
    message_ts = value.get("message_ts") if isinstance(value, Mapping) else None
    if isinstance(message_ts, str) and message_ts:
        await restore_slack_thinking_status(channel_id, message_ts)


async def show_slack_thinking_status(
    *,
    client: LangGraphClient,
    thread_id: str,
    run_id: str,
    channel_id: str,
    thread_ts: str,
    session_ts: str = "",
) -> None:
    """Keep Slack's animated "Thinking..." status alive until the run ends.

    A session (a DM) has no thread to hang the status on, so ``thread_ts`` is the
    message this run answers and ``session_ts`` names the session that owns the
    single status: claiming it moves the status off the message that held it, so
    the indicator is always on the latest one and only there.

    Slack stops the animation when the assistant posts a message, so the status
    is refreshed in the background for the whole run; the completion webhook
    clears it once no run is left.
    """
    if session_ts:
        previous = await _claim_status_anchor(client, channel_id, session_ts, thread_ts)
        if previous and previous != thread_ts:
            await set_slack_thread_status(channel_id, previous, "")
    if not await restore_slack_thinking_status(channel_id, thread_ts):
        if session_ts:
            await _release_status_anchor(client, channel_id, session_ts, thread_ts)
        return

    async def refresh() -> None:
        while True:
            await asyncio.sleep(_STATUS_REFRESH_SECONDS)
            await restore_slack_thinking_status(channel_id, thread_ts)

    refresher = asyncio.create_task(refresh())
    try:
        async with client.threads.stream(thread_id, assistant_id="agent") as thread_stream:
            async for event in thread_stream.subscribe(["lifecycle"]):
                lifecycle = root_lifecycle(event)
                if (
                    lifecycle is not None
                    and lifecycle[0] == run_id
                    and lifecycle[1] in TERMINAL_LIFECYCLE_EVENTS
                ):
                    break
    except Exception:
        logger.warning("Slack thinking status observer failed for run %s", run_id, exc_info=True)
    finally:
        refresher.cancel()
        await asyncio.shield(
            clear_slack_thinking_status_if_idle(
                client, thread_id, channel_id, thread_ts, session_ts=session_ts
            )
        )


async def clear_slack_thinking_status_if_idle(
    client: LangGraphClient,
    thread_id: str,
    channel_id: str,
    thread_ts: str,
    *,
    session_ts: str = "",
    metadata: Mapping[str, object] | None = None,
) -> None:
    """Settle an idle indicator while preserving background work and newer anchors."""
    try:
        if metadata is None:
            metadata = thread_metadata(await client.threads.get(thread_id))
        if await _thread_has_active_runs(client, thread_id):
            return
        waiting = bool(metadata.get(RUNNING_BACKGROUND_TASKS_KEY))
        if session_ts:
            item = await client.store.get_item((_STATUS_ANCHOR_NAMESPACE, channel_id), session_ts)
            value = item.get("value") if isinstance(item, Mapping) else None
            if not isinstance(value, Mapping) or value.get("message_ts") != thread_ts:
                return
            if not waiting and not await _release_status_anchor(
                client, channel_id, session_ts, thread_ts
            ):
                return
        await set_slack_thread_status(
            channel_id, thread_ts, "Waiting for background tasks…" if waiting else ""
        )
    except Exception:
        logger.warning(
            "Could not settle Slack status", extra={"agent_thread_id": thread_id}, exc_info=True
        )


async def sync_slack_background_status(
    client: LangGraphClient,
    thread_id: str,
    *,
    resume: bool = False,
    metadata: Mapping[str, object] | None = None,
    source_context: SourceContext | None = None,
) -> None:
    """Refresh the indicator using a current snapshot or a destination-only hint.

    A source context without Slack is authoritative; None means it is unknown.
    A destination hint never substitutes for task metadata when settling idle work.
    """
    try:
        if source_context is None:
            if metadata is None:
                metadata = thread_metadata(await client.threads.get(thread_id))
            source_context = SourceContext.from_metadata(metadata)
        slack_thread = source_context.slack_thread
        if slack_thread is None or not slack_thread.location:
            return
        channel_id, thread_ts = slack_thread.location
        session_ts = ""
        if thread_ts == "0":
            session_ts = thread_ts
            item = await client.store.get_item((_STATUS_ANCHOR_NAMESPACE, channel_id), session_ts)
            value = item.get("value") if isinstance(item, Mapping) else None
            anchor = value.get("message_ts") if isinstance(value, Mapping) else None
            if not isinstance(anchor, str) or not anchor:
                return
            thread_ts = anchor
        if await _thread_has_active_runs(client, thread_id):
            if resume:
                await restore_slack_thinking_status(channel_id, thread_ts)
        else:
            await clear_slack_thinking_status_if_idle(
                client, thread_id, channel_id, thread_ts, session_ts=session_ts, metadata=metadata
            )
    except Exception:
        logger.warning(
            "Could not refresh Slack background status",
            extra={"agent_thread_id": thread_id},
            exc_info=True,
        )


async def _thread_has_active_runs(client: LangGraphClient, thread_id: str) -> bool:
    try:
        for status in ("pending", "running"):
            if await client.runs.list(thread_id, status=status, limit=1):
                return True
    except Exception:  # noqa: BLE001
        logger.debug("Could not list runs for thread %s", thread_id, exc_info=True)
        return True
    return False


async def _refresh_thinking_status(channel_id: str, thread_ts: str) -> None:
    """Re-assert the status periodically; Slack drops it on each assistant message."""
    while True:
        await asyncio.sleep(_STATUS_REFRESH_SECONDS)
        await set_slack_thread_status(channel_id, thread_ts, _THINKING_STATUS)
