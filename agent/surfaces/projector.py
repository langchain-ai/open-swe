"""One streaming Slack message carrying what a run said and did.

The web dashboard needs nothing to show a run: it reads the thread. A Slack
session has to be told, so a run's words go out as streamed text and its tool
calls as task cards interleaved with them.

Who drives this matters. The run is durable — the platform re-queues it if its
pod dies and `durability="sync"` resumes it from its last checkpoint — so the
delivery has to be durable too, which means running *inside* the run
(`agent.middleware.slack_transcript`) rather than watching it from outside. An
observer in the API process would die with that process and leave a live run
talking to nobody, with nothing to restart it.

`message_ts` and `streamed_chars` are therefore recoverable: a resumed run
rehydrates them and keeps appending to the message it was already writing.
"""

import hashlib
import logging
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import PurePath
from time import monotonic
from typing import Any, Literal, cast

from langgraph_sdk.client import LangGraphClient

from agent.utils.slack import (
    SlackStreamError,
    append_slack_stream,
    start_slack_stream,
    stop_slack_stream,
    store_slack_message_run_mapping,
)

logger = logging.getLogger(__name__)

TRANSCRIPT_NAMESPACE = "slack_transcript"


def transcript_namespace(thread_id: str) -> tuple[str, str]:
    """Where a thread's per-turn transcript records live."""
    return (TRANSCRIPT_NAMESPACE, thread_id)


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


def _one_line(value: str, limit: int = 120) -> str:
    """A card title's worth of a tool argument."""
    line = " ".join(value.split())
    return f"{line[: limit - 1]}…" if len(line) > limit else line


def _tool_step(name: str, tool_input: Any) -> tuple[str, str]:
    """A step's title and detail — what it did, not which tool did it.

    The title carries the argument that identifies the step, because that is
    what a reader scans for: the command, the path, the query.
    """
    if name in {"read_file", "write_file", "edit_file", "delete"}:
        action = {
            "read_file": "Read",
            "write_file": "Wrote",
            "edit_file": "Edited",
            "delete": "Removed",
        }[name]
        return f"{action} {_basename(_text_arg(tool_input, 'file_path'))}", ""
    if name in {"execute", "background_execute"}:
        command = _one_line(_text_arg(tool_input, "command"))
        return command or "Ran a command", ""
    if name == "grep":
        pattern = _one_line(_text_arg(tool_input, "pattern"), 80)
        return f"Searched for {pattern}" if pattern else "Searched the repository", ""
    if name == "glob":
        pattern = _one_line(_text_arg(tool_input, "pattern"), 80)
        return f"Looked for {pattern}" if pattern else "Listed matching files", ""
    if name == "ls":
        return f"Listed {_one_line(_text_arg(tool_input, 'path'), 80) or 'the checkout'}", ""
    if name == "fetch_url":
        return f"Read {_one_line(_text_arg(tool_input, 'url'), 100)}".strip(), ""
    if name == "web_search":
        query = _one_line(_text_arg(tool_input, "query"), 80)
        return f"Searched the web for {query}" if query else "Searched the web", ""
    if name == "task":
        agent = _text_arg(tool_input, "subagent_type").replace("-", " ")
        return f"Delegated to {agent or 'a specialist'}", ""
    labels = {
        "open_pull_request": "Opened a pull request",
        "request_pr_review": "Started a pull request review",
        "save_plan": "Published the plan",
        "analyzePlan": "Checked the change for security issues",
    }
    return labels.get(name, f"Used {name.replace('_', ' ')}"), ""


#: Tools whose whole effect lands in this Slack session — a card for them would
#: describe something the reader is already looking at.
_SELF_EVIDENT_TOOLS = frozenset({"ask_user_choice", "manage_code_channel"})


def shows_its_own_effect(tool_name: str) -> bool:
    return tool_name.startswith("slack_") or tool_name in _SELF_EVIDENT_TOOLS


def _step_id(run_id: str, namespace: tuple[str, ...], call_id: str) -> str:
    value = "\0".join((run_id, *namespace, call_id))
    return f"step-{hashlib.sha256(value.encode()).hexdigest()[:24]}"


def _part_value(part: Any, key: str) -> Any:
    return part.get(key) if isinstance(part, dict) else getattr(part, key, None)


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
        # Keyed by task id, which is what a restored card carries.
        self.steps: dict[str, Step] = {}
        # Chunks in the order they happened, so text and task cards interleave the
        # way the run did. A step that updates replaces its earlier pending chunk
        # in place: Slack identifies a task card by id, not by position.
        self.pending: list[dict[str, Any]] = []
        self.pending_steps: dict[str, int] = {}
        self.roles: dict[tuple[str, ...], str] = {}
        self.plan_named = False
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

    def restore_pending(self, chunks: list[dict[str, Any]]) -> None:
        """Take back a queue that outlived the process that built it.

        The cards in it have to be recognized as the same cards, or a tool that
        started before the resume and finishes after it draws a second, generic
        card instead of completing the one already on screen.
        """
        self.pending = list(chunks)
        self.pending_steps = {
            str(chunk["id"]): index
            for index, chunk in enumerate(self.pending)
            if chunk.get("type") == "task_update" and chunk.get("id")
        }
        self.steps = {
            str(chunk["id"]): Step(
                task_id=str(chunk["id"]),
                title=str(chunk.get("title") or "Agent step"),
                status=cast(StepStatus, chunk.get("status") or "in_progress"),
                details=str(chunk.get("details") or ""),
                output=str(chunk.get("output") or ""),
            )
            for chunk in self.pending
            if chunk.get("type") == "task_update" and chunk.get("id")
        }

    def _queue_text(self, text: str) -> None:
        """Queue words, split so no single chunk can exceed Slack's text cap.

        A chunk over the cap is rejected outright, which would drop the whole
        message rather than shorten it.
        """
        if not text:
            return
        if self.pending and self.pending[-1].get("type") == "markdown_text":
            room = _STREAM_TEXT_LIMIT - len(self.pending[-1]["text"])
            if room > 0:
                self.pending[-1]["text"] += text[:room]
                text = text[room:]
        while text:
            self.pending.append({"type": "markdown_text", "text": text[:_STREAM_TEXT_LIMIT]})
            text = text[_STREAM_TEXT_LIMIT:]

    async def start(self) -> bool:
        """Open the message this turn is written into, with nothing in it yet.

        Steps go into one plan block rather than a card per call interleaved
        with the prose: a channel is read as a conversation, and a card for
        every shell command buries the conversation in it. The session's own
        status already says the agent is working, so there is no opening card
        either.
        """
        try:
            self.message_ts = await start_slack_stream(
                self.channel_id,
                self.thread_ts,
                [],
                recipient_user_id=self.recipient_user_id,
                recipient_team_id=self.recipient_team_id,
                task_display_mode="plan",
            )
        except SlackStreamError as exc:
            logger.info("Slack run projection unavailable for run %s: %s", self.run_id, exc.code)
            return False
        await self._map_message(self.message_ts)
        return True

    async def _map_message(self, message_ts: str) -> None:
        """Map the message being streamed into to the run behind it.

        No run id is passed: the thread's existing mapping holds the platform run
        id, and `run_id` here is a dispatch-scoped key that feedback and run
        lookups would misread as a LangSmith run.
        """
        await store_slack_message_run_mapping(
            self.client,
            self.channel_id,
            self.mapping_thread_ts,
            message_ts,
            triggering_user_id=self.recipient_user_id or None,
        )

    def say(self, text: str) -> None:
        """Queue words the agent has committed to saying."""
        self._queue_text(text)

    def name_plan(self, title: str) -> None:
        """Title the plan block the steps collect into."""
        if self.plan_named or not title.strip():
            return
        self.plan_named = True
        self.pending.append({"type": "plan_update", "title": title.strip()[:256]})

    def tool_started(self, call_id: str, tool_name: str, tool_input: Any) -> None:
        title, details = _tool_step(tool_name, tool_input)
        task_id = _step_id(self.run_id, (), call_id)
        step = Step(task_id, title, "in_progress", details)
        self.steps[task_id] = step
        self._queue_step(step)

    def tool_finished(self, call_id: str, *, failed: bool = False) -> None:
        task_id = _step_id(self.run_id, (), call_id)
        step = self.steps.get(task_id)
        if step is None:
            step = Step(task_id, "Agent step", "complete")
            self.steps[task_id] = step
        # The status renders on its own; a "Completed" line under every step
        # says it twice.
        step.status = "error" if failed else "complete"
        self._queue_step(step)

    async def flush(self, *, force: bool = False) -> None:
        if self.disabled or not self.message_ts or not self.pending:
            return
        now = monotonic()
        if now < self.retry_at or (not force and now - self.last_flush < _FLUSH_INTERVAL_SECONDS):
            return
        # Send as much as the message being written can still hold, roll over,
        # and carry on with the rest: one flush can outgrow one Slack message.
        while self.pending:
            batch, remainder, text_chars = self._next_batch()
            if not batch:
                if not await self._roll_over():
                    return
                continue
            try:
                await append_slack_stream(self.channel_id, self.message_ts, batch)
            except SlackStreamError as exc:
                if exc.code == "rate_limited":
                    delay = (
                        exc.retry_after if exc.retry_after is not None else _DEFAULT_RETRY_SECONDS
                    )
                    self.retry_at = monotonic() + min(max(delay, 1.0), _MAX_RETRY_SECONDS)
                else:
                    logger.warning(
                        "Disabling Slack run projection for %s: %s", self.run_id, exc.code
                    )
                    self.disabled = True
                return
            self.pending = remainder
            self.pending_steps.clear()
            self.streamed_chars += text_chars
        self.last_flush = monotonic()
        self.retry_at = 0.0

    def _next_batch(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        """The leading chunks that still fit the current message, and the rest."""
        batch: list[dict[str, Any]] = []
        text_chars = 0
        for index, chunk in enumerate(self.pending):
            length = len(chunk.get("text", "")) if chunk.get("type") == "markdown_text" else 0
            if length and self.streamed_chars + text_chars + length > _STREAM_TEXT_LIMIT:
                return batch, self.pending[index:], text_chars
            batch.append(chunk)
            text_chars += length
        return batch, [], text_chars

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
                task_display_mode="plan",
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
                if status != "success":
                    step.output = "Interrupted"
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


async def close_transcript(client: LangGraphClient, *, thread_id: str, run_key: str) -> None:
    """Close out a turn's streaming message, whatever happened to the run.

    Read from the middleware's own record rather than the Slack run mappings:
    the run key is the dispatch id the transcript is stored under, and stopping
    a stream that has already stopped is not an error.
    """
    try:
        item = await client.store.get_item(transcript_namespace(thread_id), key=run_key)
    except Exception:  # noqa: BLE001
        logger.debug("No transcript record for %s", run_key, exc_info=True)
        return
    value = item.get("value") if isinstance(item, Mapping) else None
    record = value if isinstance(value, dict) else {}
    message_ts = record.get("message_ts")
    channel_id = record.get("channel_id")
    if not isinstance(message_ts, str) or not isinstance(channel_id, str):
        return
    if not message_ts or not channel_id or record.get("done"):
        return
    # Whatever Slack held back on a rate limit is still in the record, and this
    # is the last chance to say it: closing the stream without it would drop the
    # words the run had already committed to.
    pending = record.get("pending")
    chunks = (
        [chunk for chunk in pending if isinstance(chunk, dict)] if isinstance(pending, list) else []
    )
    with suppress(SlackStreamError):
        await stop_slack_stream(channel_id, message_ts, chunks)
