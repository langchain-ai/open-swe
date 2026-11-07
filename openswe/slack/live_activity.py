"""Keep one Slack message showing a run's latest thoughts and tool calls.

The message replaces the "Thinking..." status for people with the Live activity
experiment. It shows what the dashboard's folded run shows, selected by the same
rules (``WorkTail`` in ``ui/src/features/agents/components/messages/renderItems.ts``),
carries a Stop button, and becomes the run's final reply when the agent posts it.
"""

import asyncio
import logging
import math
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePath
from time import monotonic
from typing import Any, Literal

from openswe.dashboard.profiles import get_profile
from openswe.slack import blocks
from openswe.slack.client import (
    SlackThreadLock,
    delete_slack_message,
    post_slack_thread_message_with_ts,
    update_slack_message,
)
from openswe.slack.http import SlackRequestError
from openswe.slack.records import SlackRun
from openswe.slack.stop import process_agent_session_stopped
from openswe.slack.thinking import restore_slack_thinking_status, tool_step
from openswe.transcript import listener
from openswe.transcript.snapshot import MessageView, ToolCallView, TranscriptSnapshot
from openswe.transcript.turns import run_turn
from openswe.utils.dashboard_links import dashboard_thread_url

logger = logging.getLogger(__name__)

STOP_ACTION = "open_swe_live_activity_stop"
_HEADING_LINE = re.compile(r"^\s*(?:#{1,6}\s+(.+?)|\*\*([^*]+)\*\*|__([^_]+)__)\s*$")
_LIST_MARKER = re.compile(r"^\s*(?:#+|[-*]|\d+\.)\s+")
_COMMAND_TOOLS = frozenset({"execute", "background_execute"})
_MIN_UPDATE_SECONDS = 1.5
# Long enough for the elapsed time to keep moving while a tool runs quietly.
_IDLE_REFRESH_SECONDS = 10.0
# About ten minutes of a queued run waiting its turn, as Slack Thinking Steps allow.
_TURN_WAIT_SECONDS = 600.0
_MAX_RETRY_SECONDS = 60.0
_RETRY_AFTER = re.compile(r"rate_limited: ([0-9.]+)")


@dataclass(frozen=True)
class ActivityEntry:
    """One entry of the live view: a thought's latest passage, or a tool call."""

    kind: Literal["reasoning", "tool"]
    title: str
    body: str = ""
    status: str = ""

    @property
    def lines(self) -> int:
        if self.kind == "tool":
            return 1
        body_lines = min(
            LiveActivity.THOUGHT_LINES, math.ceil(len(self.body) / LiveActivity.CHARS_PER_LINE)
        )
        return (1 if self.title else 0) + body_lines

    def block(self) -> blocks.ContextBlock:
        if self.kind == "tool":
            glyph = {"completed": "✓", "error": "✗"}.get(self.status, "◦")
            detail = f"  `{blocks.escape(self.body.replace('`', "'"))}`" if self.body else ""
            return blocks.context(f"{glyph} *{blocks.escape(self.title)}*{detail}")
        limit = LiveActivity.THOUGHT_LINES * LiveActivity.CHARS_PER_LINE
        body = self.body if len(self.body) <= limit else f"{self.body[: limit - 1].rstrip()}…"
        heading = f"*{blocks.escape(self.title)}*\n" if self.title else ""
        return blocks.context(f":thought_balloon: {heading}{blocks.escape(body)}")


class LiveActivity:
    """A run's newest thoughts and tool calls that fit ``MAX_LINES`` together.

    A tool call takes one line; a thought takes its latest heading and up to
    ``THOUGHT_LINES`` of its last paragraph. Only the run's own work counts, not
    what its subagents did inside their calls.
    """

    MAX_LINES = 10
    THOUGHT_LINES = 3
    CHARS_PER_LINE = 80
    MAX_PASSAGE_CHARS = 600

    def __init__(self, messages: Sequence[MessageView], tool_calls: Sequence[ToolCallView]):
        timeline: list[tuple[datetime, ActivityEntry]] = []
        for message in messages:
            if message.role == "ai" and not message.namespace and message.reasoning.strip():
                timeline.append((message.created_at, LiveActivity.thought(message.reasoning)))
        for call in tool_calls:
            if call.namespace:
                continue
            title, details = tool_step(call.name, call.input)
            command = LiveActivity.program(details) if call.name in _COMMAND_TOOLS else ""
            timeline.append((call.started_at, ActivityEntry("tool", title, command, call.status)))
        timeline.sort(key=lambda item: item[0])
        entries = [entry for _, entry in timeline if entry.title or entry.body]
        self.action_count = sum(1 for entry in entries if entry.kind == "tool")
        used = 0
        first = len(entries)
        while first > 0 and used + entries[first - 1].lines <= self.MAX_LINES:
            first -= 1
            used += entries[first].lines
        self.entries = entries[first:]

    @staticmethod
    def program(command: str) -> str:
        """The program a shell command runs, without its arguments or leading variable
        assignments, which is where tokens and other secrets tend to appear."""
        for word in command.split():
            if "=" not in word:
                return PurePath(word).name[:40]
        return ""

    @classmethod
    def thought(cls, reasoning: str) -> ActivityEntry:
        """The latest heading and the last paragraph written under it, as plain text."""
        lines = reasoning.strip().split("\n")
        # The last line may still be streaming, so it is never taken for a heading.
        heading_at = next(
            (index for index in range(len(lines) - 2, -1, -1) if _HEADING_LINE.match(lines[index])),
            None,
        )
        heading = ""
        if heading_at is not None:
            match = _HEADING_LINE.match(lines[heading_at])
            heading = cls.plain(next(group for group in match.groups() if group) if match else "")
        under = "\n".join(lines[heading_at + 1 if heading_at is not None else 0 :])
        paragraphs = [part for part in re.split(r"\n\s*\n", under) if part.strip()]
        last = paragraphs[-1] if paragraphs else ""
        body = " ".join(_LIST_MARKER.sub("", line) for line in last.split("\n"))
        return ActivityEntry("reasoning", heading, cls.plain(body)[: cls.MAX_PASSAGE_CHARS])

    @staticmethod
    def plain(text: str) -> str:
        """Markdown emphasis and code markers stripped, as the dashboard shows them."""
        text = re.sub(r"[*`]", "", text)
        return re.sub(r"(^|\W)_+|_+(?=\W|$)", r"\1", text).strip()

    @staticmethod
    def elapsed(seconds: float) -> str:
        whole = int(seconds)
        if whole < 60:
            return f"{whole}s"
        minutes, rest = divmod(whole, 60)
        if minutes < 60:
            return f"{minutes}m {rest}s" if rest else f"{minutes}m"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"

    def blocks(
        self, *, elapsed_seconds: float, thread_id: str, queued: bool = False
    ) -> list[dict[str, Any]]:
        elapsed = LiveActivity.elapsed(elapsed_seconds)
        actions = f" · {self.action_count} action{'' if self.action_count == 1 else 's'}"
        header = (
            f":hourglass: *Queued* behind the current run · {elapsed}"
            if queued
            else f":hourglass_flowing_sand: *Working*{actions} · {elapsed}"
        )
        controls: list[blocks.ActionElement] = [
            blocks.button("Stop", action_id=STOP_ACTION, style="danger")
        ]
        if url := dashboard_thread_url(thread_id):
            controls.append(
                blocks.button("Open in Robin", action_id="open_swe_live_activity_open", url=url)
            )
        return blocks.block_payload(
            [
                blocks.context(header),
                *(entry.block() for entry in self.entries),
                blocks.actions(*controls),
            ]
        )


class SlackLiveMessage:
    """The live message of one run in one Slack thread, kept current until the run ends.

    Its ts lives on the run's ``slack_run`` row, so whichever replica serves the
    agent's Slack reply can take it over; every write holds the thread's mutation
    lock, so an update never lands on a message the reply has already replaced.
    """

    def __init__(self, *, thread_id: str, run_id: str, channel_id: str, thread_ts: str) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.channel_id = channel_id
        self.thread_ts = thread_ts
        self.started = monotonic()
        self.retry_at = 0.0
        self.rendered: list[dict[str, Any]] | None = None

    @classmethod
    async def enabled_for(cls, github_login: str) -> bool:
        profile = await get_profile(github_login) if github_login else None
        return bool(profile and profile.get("experimental_live_activity"))

    @classmethod
    async def take_over(
        cls,
        channel_id: str,
        thread_ts: str,
        run_id: str,
        *,
        reply: Callable[[str], Awaitable[None]],
    ) -> str | None:
        """Hand the run's live message to its final reply; None when it has none.

        The caller holds the thread's mutation lock, so the live view cannot
        overwrite the reply once ``reply`` has edited the message into it.
        """
        message_ts = await SlackRun.live_message(channel_id, run_id)
        if message_ts is None:
            return None
        await reply(message_ts)
        await SlackRun.set_live_message(channel_id, thread_ts, run_id, None)
        return message_ts

    @classmethod
    async def move_below(cls, channel_id: str, thread_ts: str, run_id: str) -> bool:
        """Repost the run's live message under a reply it just posted; whether it had one.

        The caller holds the thread's mutation lock; the live view fills the new
        message on its next refresh.
        """
        message_ts = await SlackRun.live_message(channel_id, run_id)
        if message_ts is None:
            return False
        # Posted before the old one goes, so a failed post leaves the old one live.
        moved_ts = await post_slack_thread_message_with_ts(
            channel_id,
            thread_ts,
            "Working…",
            blocks=blocks.block_payload([blocks.context("Working…")]),
        )
        await SlackRun.set_live_message(channel_id, thread_ts, run_id, moved_ts)
        await delete_slack_message(channel_id, message_ts)
        return True

    @classmethod
    async def stop_clicked(
        cls,
        *,
        channel_id: str,
        thread_ts: str,
        message_ts: str,
        user_id: str,
        team_id: str,
        action_ts: str,
    ) -> None:
        """Stop the thread for a Stop click on a message that is still a run's live view.

        A message whose run already ended (one Slack would not let us delete, say)
        must not stop whatever runs in the thread next.
        """
        if not await SlackRun.is_live_message(channel_id, message_ts):
            logger.info("Ignoring Stop on a finished live activity message")
            return
        await process_agent_session_stopped(
            {"channel": channel_id, "thread_ts": thread_ts, "user": user_id},
            f"live-activity-stop:{channel_id}:{message_ts}:{action_ts}",
            team_id,
        )

    async def run(self) -> bool:
        """Follow the run to its end; False when the message could not be posted at all."""
        try:
            if not await self.post():
                return False
            outcome = await self.follow()
        except asyncio.CancelledError:
            # Cancelled with this process (a deploy, say) while the run goes on elsewhere.
            await asyncio.shield(self.lose_track())
            raise
        except Exception:
            logger.warning(
                "Slack live activity stopped following its run",
                extra={"agent_thread_id": self.thread_id, "run_id": self.run_id},
                exc_info=True,
            )
            # The run's outcome is unknown, so the message goes rather than guessing it.
            outcome = "lost"
        if outcome == "lost":
            await self.lose_track()
        else:
            await self.settle(outcome)
        return True

    async def lose_track(self) -> None:
        """Hand the thread back to Slack's own indicator, since the run may still be going."""
        await self.settle("lost")
        await restore_slack_thinking_status(self.channel_id, self.thread_ts)

    async def post(self) -> bool:
        placeholder = LiveActivity([], []).blocks(elapsed_seconds=0, thread_id=self.thread_id)
        try:
            message_ts = await post_slack_thread_message_with_ts(
                self.channel_id, self.thread_ts, "Working…", blocks=placeholder
            )
        except SlackRequestError as exc:
            logger.info("Slack live activity unavailable", extra={"slack_error": exc.code})
            return False
        await SlackRun.set_live_message(self.channel_id, self.thread_ts, self.run_id, message_ts)
        self.rendered = placeholder
        return True

    async def follow(self) -> str:
        """Refresh on every transcript change until the run's turn closes."""
        deadline = monotonic() + _TURN_WAIT_SECONDS
        async with listener.subscribe(self.thread_id) as versions:
            while True:
                turn = await run_turn(self.thread_id, self.run_id)
                if turn is None:
                    if monotonic() > deadline:
                        return "lost"
                    if not await self.update(LiveActivity([], []), queued=True):
                        return "replied"
                else:
                    if not turn.open:
                        return turn.state
                    messages, tool_calls = await TranscriptSnapshot.turn_contents(
                        self.thread_id, turn.turn_id
                    )
                    if not await self.update(LiveActivity(messages, tool_calls)):
                        return "replied"
                try:
                    async with asyncio.timeout(_IDLE_REFRESH_SECONDS):
                        await anext(versions)
                except TimeoutError:
                    pass
                await asyncio.sleep(max(_MIN_UPDATE_SECONDS, self.retry_at - monotonic()))

    async def update(self, activity: LiveActivity, *, queued: bool = False) -> bool:
        """Show ``activity``; False once the run's reply has taken the message over."""
        rendered = activity.blocks(
            elapsed_seconds=monotonic() - self.started, thread_id=self.thread_id, queued=queued
        )
        try:
            async with SlackThreadLock.held(self.channel_id, self.thread_ts):
                message_ts = await SlackRun.live_message(self.channel_id, self.run_id)
                if message_ts is None:
                    return False
                if rendered == self.rendered:
                    return True
                await update_slack_message(self.channel_id, message_ts, "Working…", blocks=rendered)
        except TimeoutError:
            # A reply is holding the thread; the next refresh shows whatever follows it.
            return True
        except SlackRequestError as exc:
            if exc.code == "message_not_found":
                await SlackRun.set_live_message(self.channel_id, self.thread_ts, self.run_id, None)
                return False
            if exc.code.startswith("rate_limited"):
                delay = _RETRY_AFTER.search(exc.code)
                self.retry_at = monotonic() + min(
                    float(delay[1]) if delay else _MIN_UPDATE_SECONDS * 4, _MAX_RETRY_SECONDS
                )
            else:
                logger.warning("Slack live activity update failed", extra={"slack_error": exc.code})
            return True
        self.rendered = rendered
        return True

    async def settle(self, outcome: str) -> None:
        """Leave a one-line summary where the live view was, unless a reply replaced it."""
        try:
            async with SlackThreadLock.held(self.channel_id, self.thread_ts):
                message_ts = await SlackRun.live_message(self.channel_id, self.run_id)
                if message_ts is None:
                    return
                await SlackRun.set_live_message(self.channel_id, self.thread_ts, self.run_id, None)
                elapsed = LiveActivity.elapsed(monotonic() - self.started)
                if outcome not in {"interrupted", "failed"}:
                    # Finished without a final reply, or lost track of the run: the
                    # thread is left as it was, or at least without live controls.
                    if not await delete_slack_message(self.channel_id, message_ts):
                        await update_slack_message(
                            self.channel_id,
                            message_ts,
                            "Finished",
                            blocks=blocks.block_payload([blocks.context("Finished")]),
                        )
                    return
                summary = (
                    f"Stopped after {elapsed}"
                    if outcome == "interrupted"
                    else f"Failed after {elapsed}"
                )
                await update_slack_message(
                    self.channel_id,
                    message_ts,
                    summary,
                    blocks=blocks.block_payload([blocks.context(summary)]),
                )
        except Exception:
            logger.warning(
                "Could not settle Slack live activity",
                extra={"agent_thread_id": self.thread_id, "run_id": self.run_id},
                exc_info=True,
            )
