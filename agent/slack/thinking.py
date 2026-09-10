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
    start_slack_stream,
    stop_slack_stream,
    store_slack_run_mapping,
)
from agent.utils.streaming import TERMINAL_LIFECYCLE_EVENTS, root_lifecycle

logger = logging.getLogger(__name__)

StepStatus = Literal["in_progress", "complete", "error"]
_FLUSH_INTERVAL_SECONDS = 1.0
_DEFAULT_RETRY_SECONDS = 30.0
_MAX_RETRY_SECONDS = 300.0


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
