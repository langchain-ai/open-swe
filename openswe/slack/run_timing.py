"""How long a Slack message takes to reach its run's first output.

Every mark is measured from when the person posted the message, by Slack's own clock
(the run's ``slack_trigger_ts``):

- ``first_generation``: the model opened its first reply, as Managed Deep Agents
  stamped it (``DashboardRunTimeline.output_marks``).
- ``first_reply``: the agent's first reply appeared in the Slack thread, by the Slack
  timestamp of the first message its ``slack_reply`` tool posted, progress or final.
  Slack shows no streamed text, so this is the first text the person sees.

The run's completion webhook reads both from the run's own replies, and each goes to an
OTLP latency histogram labelled by sandbox, once per run. When MDA's Slack channel opened a
LangSmith trace for the message (``SlackSendTrace``), the webhook also adds a span per stage
the run went through and closes the trace. Two spans there time the whole wait from the
message: to the run's start (``time_to_run_start``), when Trigger Server shows the agent's
working status in the thread (on MDA's ``run.started``), and to the first reply
(``time_to_first_reply``).
"""

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import timedelta
from itertools import pairwise
from typing import ClassVar, Final

from runtime_api.slack import SEND_DOTTED_ORDER_KEY, SEND_PROJECT_KEY, SEND_TRACE_ID_KEY

from openswe.dashboard.ttft import DashboardRunTimeline, DashboardSendTrace
from openswe.invocation import resolve_invocation_id

logger = logging.getLogger(__name__)

# The run metadata naming the Slack message that started the run.
TRIGGER_TS_KEY: Final = "slack_trigger_ts"
# The thread metadata naming its latest runs whose Slack timing is reported.
TIMED_RUNS_KEY: Final = "slack_timed_run_ids"
# The tool the agent answers in its Slack thread with.
_REPLY_TOOL: Final = "slack_reply"
# A run created this long after the message it names inherited that metadata.
_MAX_START_DELAY: Final = timedelta(minutes=2)
_METRICS: Final[dict[str, str]] = {
    "first_generation": "open_swe_slack_thread_first_generation",
    "first_reply": "open_swe_slack_thread_first_reply",
}


class SlackSendTrace(DashboardSendTrace):
    """A Slack message's LangSmith trace, from its posting to the end of the run it starts.

    MDA's Slack channel opens it as the message arrives, with a span for each step its
    handler takes (``rt.slack``); the backend's answers to the handler nest under those.
    The run's completion webhook takes it up from the run's metadata, adds the run's
    stages and closes it.
    """

    NAME = "slack.send"
    TRACE_ID_KEY = SEND_TRACE_ID_KEY
    DOTTED_ORDER_KEY = SEND_DOTTED_ORDER_KEY
    _SPAN_KEYS = ("thread_id", "invocation_id")
    # The run is a root of its own that carries its attribution, so the message's root
    # takes none on as it closes; counting both would count the turn twice.
    _CLOSE_KEYS: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def project_of(cls, metadata: Mapping[str, object]) -> str:
        """The project MDA's deployment opened the trace in, which its own runs trace to."""
        project = metadata.get(SEND_PROJECT_KEY)
        return project if isinstance(project, str) and project else super().project_of(metadata)


class SlackRunTimeline:
    """One Slack message's marks, read from the run it started."""

    # Each span the completion adds to the message's trace, named after the point it ends at.
    # The channel traced what came before the run was created itself, as it ran.
    _STAGES: Final[dict[str, str]] = {
        "first_generation": "first_generation",
        "first_reply": "first_reply",
    }
    # The span from the message to its run's start, when Slack shows the working status.
    RUN_START: ClassVar[str] = "time_to_run_start"
    _settling: ClassVar[set[asyncio.Task[None]]] = set()

    def __init__(
        self,
        posted_at_ms: int,
        *,
        run_id: str,
        started_at_ms: int | None = None,
        created_at_ms: int | None = None,
        trace: SlackSendTrace | None = None,
    ) -> None:
        self.posted_at_ms = posted_at_ms
        self.run_id = run_id
        # Output before the run started is an earlier run's: a message sent while one
        # worked queues a run that starts only once that one ends.
        self._since_ms = max(posted_at_ms, started_at_ms or 0)
        self.started_at_ms = started_at_ms
        self.created_at_ms = created_at_ms
        self.trace = trace
        self.marks: dict[str, int] = {}

    @staticmethod
    def ts_ms(value: object) -> int | None:
        """A Slack timestamp (``"1731600000.123456"``, epoch seconds) in epoch ms."""
        if not isinstance(value, str) or not value:
            return None
        try:
            return round(float(value) * 1000)
        except ValueError:
            return None

    @classmethod
    def of_run(cls, run: Mapping[str, object]) -> SlackRunTimeline | None:
        """The Slack message a finished run names, marked from the run's messages.

        ``None`` when no Slack message started the run: none is named, the run was
        created so long after it that it inherited the metadata, or a dashboard send
        came after it and started this run. A run MDA's Slack channel handed the
        message's trace to was started by it, however late.
        """
        metadata = run.get("metadata")
        run_id = run.get("run_id")
        if not isinstance(metadata, Mapping) or not isinstance(run_id, str):
            return None
        posted = cls.ts_ms(metadata.get(TRIGGER_TS_KEY))
        if posted is None:
            return None
        thread_id = run.get("thread_id")
        trace = SlackSendTrace.of_run(
            metadata,
            thread_id=thread_id if isinstance(thread_id, str) else None,
            started_at_ms=posted,
        )
        created = DashboardSendTrace.time_of(run.get("created_at"))
        if (
            trace is None
            and created is not None
            and created.timestamp() * 1000 - posted > _MAX_START_DELAY.total_seconds() * 1000
        ):
            return None
        sent = DashboardRunTimeline.epoch_ms(metadata.get(DashboardRunTimeline.RECEIVED_AT_KEY))
        if sent is not None and sent > posted:
            return None
        started = DashboardSendTrace.time_of(run.get("run_started_at"))
        timeline = cls(
            posted,
            run_id=run_id,
            started_at_ms=round(started.timestamp() * 1000) if started is not None else None,
            created_at_ms=round(created.timestamp() * 1000) if created is not None else None,
            trace=trace,
        )
        values = run.get("values")
        messages = values.get("messages") if isinstance(values, Mapping) else None
        try:
            invocation_id = resolve_invocation_id(metadata)
        except ValueError:
            # Only output from after the run started is then counted as its own.
            logger.warning(
                "Slack run timing found unreadable invocation metadata",
                extra={"run_id": run_id},
                exc_info=True,
            )
            invocation_id = None
        timeline.read(messages if isinstance(messages, list) else [], invocation_id=invocation_id)
        return timeline

    def read(self, messages: Sequence[object], *, invocation_id: str | None = None) -> None:
        """Mark this run's first output and its first reply in Slack.

        A run's state also holds earlier runs' replies, some posted after the message: a
        message sent while a run worked queues a run that starts once that one ends. The
        run's own replies are those stamped with its invocation, which a run resumed
        after a drain keeps; without one, output from before the run started is not its.
        """
        own = [
            message
            for message in messages
            if isinstance(message, Mapping)
            and DashboardRunTimeline.message_type(message) == "ai"
            and (invocation_id is None or self._invocation_of(message) == invocation_id)
        ]
        since = self.posted_at_ms if invocation_id is not None else self._since_ms
        for name, at in DashboardRunTimeline.output_marks(own, since_ms=since).items():
            if name == "first_generation":
                self.marks[name] = min(self.marks.get(name, at), at)
        calls = {
            call["id"]
            for message in own
            for call in self._tool_calls(message)
            if call.get("name") == _REPLY_TOOL and isinstance(call.get("id"), str)
        }
        for message in messages:
            if (
                not isinstance(message, Mapping)
                or DashboardRunTimeline.message_type(message) != "tool"
                or DashboardRunTimeline.message_field(message, "name") != _REPLY_TOOL
                or DashboardRunTimeline.message_field(message, "tool_call_id") not in calls
            ):
                continue
            posted = self.ts_ms(
                self._reply_result(DashboardRunTimeline.message_field(message, "content")).get(
                    "message_ts"
                )
            )
            if posted is not None and posted >= since:
                self.marks["first_reply"] = min(self.marks.get("first_reply", posted), posted)

    @staticmethod
    def _invocation_of(reply: Mapping[str, object]) -> object:
        stamps = DashboardRunTimeline.message_field(reply, "response_metadata")
        return stamps.get("open_swe_invocation_id") if isinstance(stamps, Mapping) else None

    @staticmethod
    def _tool_calls(reply: Mapping[str, object]) -> list[Mapping[str, object]]:
        calls = DashboardRunTimeline.message_field(reply, "tool_calls")
        if not isinstance(calls, list):
            return []
        return [call for call in calls if isinstance(call, Mapping)]

    def seconds(self) -> dict[str, float]:
        """Each mark the run reached, in seconds after the message."""
        return {name: (at - self.posted_at_ms) / 1000 for name, at in self.marks.items()}

    @classmethod
    def settle_in_background(cls, run: Mapping[str, object], *, failed: bool) -> None:
        """Settle a finished run's message without holding up the rest of its completion."""
        task = asyncio.create_task(cls.settle(run, failed=failed))
        cls._settling.add(task)
        task.add_done_callback(cls._settling.discard)

    @classmethod
    async def settle(cls, run: Mapping[str, object], *, failed: bool = False) -> None:
        """Time the Slack message a finished run names, and close its trace; it never raises."""
        try:
            timeline = cls.of_run(run)
        except Exception:
            logger.warning("Could not read a Slack message's run timing", exc_info=True)
            return
        thread_id = run.get("thread_id")
        if timeline is None or not isinstance(thread_id, str):
            return
        thread = await DashboardRunTimeline.thread_metadata(thread_id, timeline.run_id)
        # A completion delivered again was timed the first time, and closed the trace.
        if (
            thread is not None
            and timeline.marks
            and not DashboardRunTimeline.timed_already(thread, timeline.run_id, key=TIMED_RUNS_KEY)
        ):
            timeline.record(thread_id=thread_id)
            await DashboardRunTimeline.mark_timed(
                thread_id, thread, timeline.run_id, key=TIMED_RUNS_KEY
            )
        if timeline.trace is not None and not await timeline.trace.ended():
            try:
                timeline.trace.record_stages(
                    [
                        *timeline.stages(),
                        *timeline.run_start_stage(),
                        *timeline.first_reply_stage(),
                    ],
                    run_id=timeline.run_id,
                )
            except Exception:
                logger.warning("Failed to trace a Slack message's stages", exc_info=True)
            await timeline.trace.close(run, failed=failed)

    def run_start_stage(self) -> list[tuple[str, int, int]]:
        """The span from the message to its run's start, once the run started.

        Slack shows the agent's working status as the run starts, so this is how long the
        person waits to see their message was picked up. A message sent while another run
        worked waits for it: its run starts only once that one ends.
        """
        if self.started_at_ms is None:
            return []
        return [(self.RUN_START, self.posted_at_ms, max(self.started_at_ms, self.posted_at_ms))]

    def first_reply_stage(self) -> list[tuple[str, int, int]]:
        """The span from the message to the first reply the person saw, once there was one."""
        first_reply = self.marks.get("first_reply")
        if first_reply is None:
            return []
        return [
            (
                DashboardSendTrace.FIRST_REPLY,
                self.posted_at_ms,
                max(first_reply, self.posted_at_ms),
            )
        ]

    def stages(self) -> list[tuple[str, int, int]]:
        """Each stage the run went through, as ``(span name, began, ended)`` in epoch ms,
        from its creation on; none ends before it starts, whatever a host's clock says."""
        if self.created_at_ms is None:
            return []
        points: list[tuple[str, int]] = [("created", self.created_at_ms)]
        for name in self._STAGES:
            if name in self.marks:
                points.append((name, max(self.marks[name], points[-1][1])))
        return [
            (self._STAGES[name], began, ended) for (_, began), (name, ended) in pairwise(points)
        ]

    def record(self, *, thread_id: str) -> None:
        seconds = self.seconds()
        try:
            DashboardRunTimeline.record_latencies(
                {_METRICS[name]: value for name, value in seconds.items()},
                attributes={"source": "slack"},
            )
        except Exception:
            logger.warning("Failed to record Slack run timing histograms", exc_info=True)
        logger.info(
            "Slack run timing",
            extra={
                "agent_thread_id": thread_id,
                "run_id": self.run_id,
                **{f"{name}_ms": round(value * 1000) for name, value in seconds.items()},
            },
        )

    @staticmethod
    def _reply_result(content: object) -> Mapping[str, object]:
        """What a ``slack_reply`` call returned, from its tool message's content."""
        if isinstance(content, list):
            # With images, the result is the first text block.
            content = next(
                (
                    block.get("text")
                    for block in content
                    if isinstance(block, Mapping) and block.get("type") == "text"
                ),
                None,
            )
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except ValueError:
                return {}
        if not isinstance(content, Mapping) or content.get("success") is not True:
            return {}
        return content
