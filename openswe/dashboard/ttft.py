"""How long a dashboard send takes to reach its run's first output.

Every mark is measured from the moment the backend received the send:

- ``first_generation``: the model opened its first assistant message, thinking or a
  tool call included, so the agent has visibly started.
- ``ttft``: the first assistant text, which a run that opens with tool calls reaches
  only after them.

Managed Deep Agents stamps each of the agent's replies with when its model call opened
its message and first streamed text (``runtime_api.loop.FIRST_OUTPUT_AT_KEY``), or with
when the call returned if it streamed nothing. The run's completion webhook reads them back from
the run's messages, so every send is timed however its client reads the run: a
transcript dashboard starts runs that stream no messages to the thread.

Each mark goes to an OTLP latency histogram labelled by client, and to a
span of the send's own LangSmith trace (``DashboardSendTrace``), which the run itself
nests in.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import TYPE_CHECKING, ClassVar, Final, Self
from urllib.parse import quote

from langsmith.run_helpers import trace, tracing_context
from langsmith.run_trees import RunTree
from langsmith.utils import tracing_is_enabled
from pydantic import JsonValue
from runtime_api.loop import FIRST_OUTPUT_AT_KEY, FIRST_TEXT_AT_KEY

from openswe.config import ENV
from openswe.threads.runtimes import MdaRuntime
from openswe.trace_metadata import BILLING_FIELDS, RunOrigin
from openswe.utils.datadog_spans import DatadogSpans
from openswe.utils.langsmith import (
    async_langsmith_client,
    resolve_project_id_by_name,
    sync_langsmith_client,
)
from openswe.utils.tracing import tracing_project

if TYPE_CHECKING:
    from langgraph_api.metrics_otlp import MetricDef
    from langsmith import AsyncClient as AsyncLangSmithClient

logger = logging.getLogger(__name__)

# A product token only the Robin desktop app's Electron shell has sent in its user agent.
_DESKTOP_PRODUCTS: Final = ("Robin/", "OpenSWE/", "open-swe-desktop/")
# The header the dashboard sends with a command: when the person sent it, in epoch ms.
SUBMITTED_AT_HEADER: Final = "x-robin-submitted-at"
# How far before the backend received it a client's send time is believed; further
# back, or ahead by more than the allowed skew, its clock is wrong.
_MAX_CLIENT_LEAD_MS: Final = 60_000
_MAX_CLIENT_SKEW_MS: Final = 2_000


class DashboardClient(StrEnum):
    """Which client sent a dashboard command, as a bounded metric label."""

    DESKTOP = "desktop"
    WEB = "web"
    OTHER = "other"

    @classmethod
    def of_user_agent(cls, user_agent: str | None) -> DashboardClient:
        if not user_agent:
            return cls.OTHER
        if "Electron/" in user_agent and any(p in user_agent for p in _DESKTOP_PRODUCTS):
            return cls.DESKTOP
        if user_agent.startswith("Mozilla/") and "Electron/" not in user_agent:
            return cls.WEB
        return cls.OTHER


class DashboardSendTrace:
    """One send's LangSmith trace, from the click to the end of the run it starts.

    The backend opens it as the send arrives, with a live ``commands`` span for the
    request that starts the run, and hands its trace headers to the run's start, so
    Managed Deep Agents nests the run inside it. The run's completion webhook
    takes it up again from the run's metadata (:meth:`of_run`), adds a span per stage the
    send went through, and closes it with the run's end, last message and error
    (:meth:`close`). It carries the thread and invocation the run's root would, so the
    thread view, error links and cost lookups reach the run through it.
    """

    # The baggage metadata key Managed Deep Agents adopts trace headers on.
    _NEST_UNDER_PARENT: Final = "ls_nest_under_parent"
    # The trace's root span.
    NAME: ClassVar[str] = "dashboard.send"
    # The span from a message to the first reply its sender sees, the stages it spans in one.
    FIRST_REPLY: ClassVar[str] = "time_to_first_reply"
    # The run metadata that names the send's trace, for its completion to close.
    TRACE_ID_KEY: ClassVar[str] = "dashboard_send_trace_id"
    DOTTED_ORDER_KEY: ClassVar[str] = "dashboard_send_dotted_order"
    # How long a close waits to learn whether the root is already closed.
    _ENDED_TIMEOUT_S: Final = 5
    # The root's metadata each stage span repeats, so stages chart by client on their own.
    _SPAN_KEYS: ClassVar[tuple[str, ...]] = ("thread_id", "invocation_id", "dashboard_client")
    # The run's metadata the root takes on as it closes: whom the run is for and what it
    # was, so charts of root traces count and bill a send like any other run. The root's
    # own keys travel too, since a run update replaces its metadata.
    _CLOSE_KEYS: ClassVar[frozenset[str]] = frozenset(
        {*_SPAN_KEYS, "origin", "owner_ls_user_id", "kind", "user_email", *BILLING_FIELDS}
    )

    def __init__(
        self, root: RunTree, commands: RunTree | None = None, *, posted: bool = True
    ) -> None:
        self.root = root
        # The span of the request that starts the run, open until the run is started.
        self.commands = commands
        # Whether LangSmith holds the root: a send's root waits for its run to be built,
        # since any update to a posted run also ends it.
        self._posted = posted

    @staticmethod
    def at(epoch_ms: int) -> datetime:
        return datetime.fromtimestamp(epoch_ms / 1000, UTC)

    @staticmethod
    def time_of(value: object) -> datetime | None:
        """An ISO 8601 time from a run payload, read as UTC when it names no zone."""
        if not isinstance(value, str) or not value:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    @classmethod
    async def open(
        cls,
        *,
        thread_id: str,
        client: DashboardClient,
        started_at_ms: int,
        received_at_ms: int,
        inputs: Mapping[str, object],
        origin: RunOrigin = RunOrigin.DASHBOARD,
    ) -> DashboardSendTrace | None:
        """Start the trace as the send arrives, before its run is built, or ``None`` when
        this backend does not trace or cannot. :meth:`adopt` names the run's invocation
        and owner once the run is built."""
        api_key = ENV.LANGSMITH_API_KEY.optional()
        if not api_key or not tracing_is_enabled():
            return None
        try:
            return cls._started(
                api_key, thread_id, client, started_at_ms, received_at_ms, inputs, origin
            )
        except Exception:
            logger.warning("Could not open a dashboard send's trace", exc_info=True)
            return None

    @classmethod
    def _started(
        cls,
        api_key: str,
        thread_id: str,
        client: DashboardClient,
        started_at_ms: int,
        received_at_ms: int,
        inputs: Mapping[str, object],
        origin: RunOrigin,
    ) -> DashboardSendTrace:
        trace_id = uuid.uuid7()
        root = RunTree(
            name=cls.NAME,
            id=trace_id,
            trace_id=trace_id,
            run_type="chain",
            start_time=cls.at(started_at_ms),
            project_name=tracing_project(),
            ls_client=sync_langsmith_client(api_key, ENV.LANGSMITH_ENDPOINT.get()),
            inputs=dict(inputs),
            extra={
                "metadata": {
                    "thread_id": thread_id,
                    "dashboard_client": client.value,
                    "origin": origin.value,
                }
            },
        )
        commands = root.create_child(
            "commands",
            run_id=cls.stage_id(trace_id, "commands"),
            start_time=cls.at(received_at_ms),
        )
        commands.post()
        return cls(root, commands, posted=False)

    @staticmethod
    def stage_id(trace_id: uuid.UUID, name: str) -> uuid.UUID:
        """A stage span's id, from its trace and name, so tracing it again rewrites it."""
        return uuid.uuid5(trace_id, name)

    async def adopt(self, *, thread_id: str, invocation_id: str | None) -> None:
        """Post the root, naming the built run's invocation and owner, which the thread
        view, cost lookups and personal trace access read; it stays open."""
        try:
            owner = await MdaRuntime.owner_ls_user_id(thread_id)
            self.root.add_metadata(
                {
                    **({"invocation_id": invocation_id} if invocation_id else {}),
                    **({"owner_ls_user_id": owner} if owner else {}),
                }
            )
        except Exception:
            logger.warning(
                "Could not name a dashboard send's run on its trace",
                extra={"dashboard_send_trace_id": self.trace_id},
                exc_info=True,
            )
        self._post_root()

    def _post_root(self) -> None:
        if self._posted:
            return
        self._posted = True
        try:
            self.root.post()
        except Exception:
            logger.warning(
                "Could not post a dashboard send's trace",
                extra={"dashboard_send_trace_id": self.trace_id},
                exc_info=True,
            )

    @contextmanager
    def stage(self, name: str) -> Generator[None]:
        """A span of the ``commands`` request while it is open, with Datadog's spans of
        the request's work under it."""
        if self.commands is None:
            yield
            return
        # The send is traced, whatever this process last read of the tracing setting.
        with (
            tracing_context(enabled=True),
            trace(name, parent=self.commands, client=self.root.client) as run,
            DatadogSpans.under(run),
        ):
            yield

    def end_commands(self, error: str | None = None) -> None:
        """Close the ``commands`` span: the run has started, or will not."""
        commands, self.commands = self.commands, None
        if commands is None:
            return
        try:
            commands.end(error=error)
            commands.patch()
        except Exception:
            logger.warning("Could not close a dashboard send's commands span", exc_info=True)

    @property
    def trace_id(self) -> str:
        return str(self.root.id)

    @property
    def root_metadata(self) -> Mapping[str, object]:
        """The metadata the root was opened or taken up with."""
        metadata = self.root.extra.get("metadata") if self.root.extra else None
        return metadata if isinstance(metadata, Mapping) else {}

    def run_metadata(self) -> dict[str, str]:
        """What the run's metadata carries so its completion can close this trace."""
        return {self.TRACE_ID_KEY: self.trace_id, self.DOTTED_ORDER_KEY: self.root.dotted_order}

    @classmethod
    def project_of(cls, metadata: Mapping[str, object]) -> str:
        """The project a run's trace is in: this backend opened it in the runs' own."""
        return tracing_project()

    @classmethod
    def of_run(
        cls, metadata: Mapping[str, object], *, thread_id: str | None, started_at_ms: int
    ) -> Self | None:
        """The trace a run's metadata names, to add to and close; its root is not posted again."""
        trace_id = metadata.get(cls.TRACE_ID_KEY)
        dotted_order = metadata.get(cls.DOTTED_ORDER_KEY)
        api_key = ENV.LANGSMITH_API_KEY.optional()
        if (
            not isinstance(trace_id, str)
            or not isinstance(dotted_order, str)
            or not api_key
            or not tracing_is_enabled()
        ):
            return None
        try:
            root = RunTree(
                name=cls.NAME,
                id=uuid.UUID(trace_id),
                trace_id=uuid.UUID(trace_id),
                dotted_order=dotted_order,
                run_type="chain",
                start_time=cls.at(started_at_ms),
                project_name=cls.project_of(metadata),
                ls_client=sync_langsmith_client(api_key, ENV.LANGSMITH_ENDPOINT.get()),
                extra={
                    "metadata": {
                        key: value
                        for key, value in {**metadata, "thread_id": thread_id}.items()
                        if key in cls._SPAN_KEYS and isinstance(value, str)
                    }
                },
            )
        except Exception:
            logger.warning(
                "Could not take up a dashboard send's trace",
                extra={"dashboard_send_trace_id": trace_id},
                exc_info=True,
            )
            return None
        return cls(root)

    async def ended(self) -> bool:
        """Whether the trace is closed already: a webhook delivered again has closed it."""
        api_key = ENV.LANGSMITH_API_KEY.optional()
        project = self.root.session_name
        project_id = await resolve_project_id_by_name(project) if api_key and project else None
        if not api_key or not project_id:
            return False
        client: AsyncLangSmithClient = async_langsmith_client(api_key, ENV.LANGSMITH_ENDPOINT.get())
        try:
            async with asyncio.timeout(self._ENDED_TIMEOUT_S):
                root = await client.runs.retrieve(
                    self.trace_id, project_id=project_id, selects=["END_TIME"]
                )
        except Exception:
            # Not ingested yet, so not closed yet.
            return False
        return root.end_time is not None

    async def close(self, run: Mapping[str, object], *, failed: bool) -> None:
        """Close the trace with the end, last message and error of the run it started."""
        api_key = ENV.LANGSMITH_API_KEY.optional()
        if not api_key:
            return
        try:
            values = run.get("values")
            messages = values.get("messages") if isinstance(values, Mapping) else None
            error = run.get("error")
            metadata = run.get("metadata")
            closing = (
                {
                    key: value
                    for key, value in {**metadata, **self.root_metadata}.items()
                    if key in self._CLOSE_KEYS and isinstance(value, str)
                }
                if isinstance(metadata, Mapping)
                else {}
            )
            # The sync client redacts outputs as configured and sends on its background batch.
            sync_langsmith_client(api_key, ENV.LANGSMITH_ENDPOINT.get()).update_run(
                self.trace_id,
                end_time=self.time_of(run.get("run_ended_at")) or datetime.now(UTC),
                outputs={"messages": messages[-1:]} if isinstance(messages, list) else None,
                error=(str(error) if error else "The run did not succeed") if failed else None,
                extra={"metadata": closing} if closing else None,
                trace_id=self.trace_id,
                dotted_order=self.root.dotted_order,
                session_name=self.root.session_name,
            )
        except Exception:
            logger.warning(
                "Could not close a dashboard send's trace",
                extra={"dashboard_send_trace_id": self.trace_id},
                exc_info=True,
            )

    def headers(self) -> dict[str, str]:
        """What the run's start carries so the run nests under this trace.

        Only the position, the project and the nesting mark travel; the root's metadata
        stays out, since a receiver would copy it onto the run.
        """
        baggage = [f"langsmith-metadata={quote(json.dumps({self._NEST_UNDER_PARENT: True}))}"]
        if self.root.session_name:
            baggage.append(f"langsmith-project={quote(self.root.session_name)}")
        return {"langsmith-trace": self.root.dotted_order, "baggage": ",".join(baggage)}

    def fail(self, error: str) -> None:
        """Close the trace of a send whose run did not start."""
        self.end_commands(error)
        try:
            self.root.end(error=error)
            if self._posted:
                self.root.patch()
            else:
                self._post_root()
        except Exception:
            logger.warning("Could not close a dashboard send's trace", exc_info=True)

    def record_stages(
        self,
        stages: Sequence[tuple[str, int, int]],
        *,
        run_id: str,
    ) -> None:
        """Post a span per stage, each with an id of its own trace and name, so tracing
        them again rewrites the same spans."""
        shared = {key: value for key, value in self.root_metadata.items() if key in self._SPAN_KEYS}
        for name, began, ended in stages:
            self.root.create_child(
                name,
                run_id=self.stage_id(uuid.UUID(self.trace_id), name),
                start_time=self.at(began),
                end_time=self.at(ended),
                outputs={"elapsed_ms": ended - began},
                extra={
                    "metadata": {
                        **shared,
                        "agent_run_id": run_id,
                    }
                },
            ).post()


class DashboardRunTimeline:
    """One dashboard send's marks, read from the replies of the run it started.

    The send's request stages end where the run was created, which is as close as
    the run's completion comes to when the backend's start of it returned.
    """

    # The run metadata the send leaves for its run's completion to time it by.
    RECEIVED_AT_KEY: Final = "dashboard_ttft_started_at_ms"
    SUBMITTED_AT_KEY: Final = "dashboard_submitted_at_ms"
    CLIENT_KEY: Final = "dashboard_client"
    # The thread metadata naming the run each of the thread's latest sends started, by the
    # send's receipt time. Later runs on the thread, a queued follow-up's or a pickup's,
    # inherit a send's run metadata; a later send may start before an earlier run's
    # completion is heard.
    STARTED_RUNS_KEY: Final = "dashboard_send_runs"
    _STARTED_RUNS_KEPT: Final = 8
    # The thread metadata naming its latest runs whose timing is reported, so a run's
    # completion delivered again reports nothing twice.
    TIMED_RUNS_KEY: Final = "timed_run_ids"
    _TIMED_RUNS_KEPT: Final = 16
    # Where the thread does not name it, a run created this long after the send it
    # names inherited that metadata.
    _MAX_START_DELAY: Final = timedelta(minutes=2)

    _METRICS: Final[dict[str, str]] = {
        "first_generation": "open_swe_dashboard_thread_first_generation",
        "ttft": "open_swe_dashboard_thread_ttft",
    }
    _definitions: ClassVar[dict[str, MetricDef]] = {}
    _settling: ClassVar[set[asyncio.Task[None]]] = set()

    # Each span of the send trace, named after the point it ends at.
    _STAGES: Final[dict[str, str]] = {
        "received": "client.request",
        "accepted": "commands",
        "first_generation": "first_generation",
        "ttft": "first_text",
    }
    # Where a reply says when its model call opened and first gave text, by the mark it sets.
    _STAMPS: Final[dict[str, str]] = {
        "first_generation": FIRST_OUTPUT_AT_KEY,
        "ttft": FIRST_TEXT_AT_KEY,
    }

    def __init__(
        self,
        received_at_ms: int,
        *,
        run_id: str,
        client: DashboardClient = DashboardClient.OTHER,
        submitted_at_ms: int | None = None,
        accepted_at_ms: int | None = None,
        trace: DashboardSendTrace | None = None,
    ) -> None:
        self.received_at_ms = received_at_ms
        self.run_id = run_id
        self.client = client
        self.submitted_at_ms = submitted_at_ms
        self.accepted_at_ms = accepted_at_ms
        self.trace = trace
        self.marks: dict[str, int] = {}

    @staticmethod
    def client_submitted_at(header: str | None, received_at_ms: int) -> int | None:
        """The client's send time, when its clock is plausible against the backend's."""
        try:
            submitted = int(header) if header else None
        except ValueError:
            return None
        if submitted is None or not (
            received_at_ms - _MAX_CLIENT_LEAD_MS
            <= submitted
            <= received_at_ms + _MAX_CLIENT_SKEW_MS
        ):
            return None
        return min(submitted, received_at_ms)

    @classmethod
    def of_run(cls, run: Mapping[str, object]) -> DashboardRunTimeline | None:
        """The send a finished run's metadata names, marked from the run's replies.

        ``None`` when no dashboard send started the run: the run was created so long
        after the send it names that it inherited the send's metadata, or a Slack message
        came after the send and started this run.
        """
        metadata = run.get("metadata")
        run_id = run.get("run_id")
        if not isinstance(metadata, Mapping) or not isinstance(run_id, str):
            return None
        received = cls.epoch_ms(metadata.get(cls.RECEIVED_AT_KEY))
        if received is None:
            return None
        created = DashboardSendTrace.time_of(run.get("created_at"))
        if created is not None and created - DashboardSendTrace.at(received) > cls._MAX_START_DELAY:
            return None
        from openswe.slack.run_timing import TRIGGER_TS_KEY, SlackRunTimeline

        posted = SlackRunTimeline.ts_ms(metadata.get(TRIGGER_TS_KEY))
        if posted is not None and posted > received:
            return None
        submitted = cls.epoch_ms(metadata.get(cls.SUBMITTED_AT_KEY))
        client = metadata.get(cls.CLIENT_KEY)
        timeline = cls(
            received,
            run_id=run_id,
            client=DashboardClient(client)
            if client in DashboardClient._value2member_map_
            else DashboardClient.OTHER,
            submitted_at_ms=submitted,
            accepted_at_ms=round(created.timestamp() * 1000) if created is not None else None,
            trace=DashboardSendTrace.of_run(
                metadata,
                thread_id=thread_id if isinstance(thread_id := run.get("thread_id"), str) else None,
                started_at_ms=submitted or received,
            ),
        )
        values = run.get("values")
        messages = values.get("messages") if isinstance(values, Mapping) else None
        timeline.read(messages if isinstance(messages, list) else [])
        return timeline

    def read(self, messages: Sequence[object]) -> None:
        """Mark the earliest output and text the agent's replies gave since the send."""
        for name, at in self.output_marks(messages, since_ms=self.received_at_ms).items():
            self.marks[name] = min(self.marks.get(name, at), at)

    @classmethod
    def output_marks(cls, messages: Sequence[object], *, since_ms: int) -> dict[str, int]:
        """The earliest opening and text the agent's replies stamped at or after ``since_ms``.

        A reply from before it is an earlier run's.
        """
        marks: dict[str, int] = {}
        for message in messages:
            stamps = (
                cls.message_field(message, "response_metadata")
                if isinstance(message, Mapping)
                else None
            )
            if not isinstance(stamps, Mapping):
                continue
            for name, key in cls._STAMPS.items():
                at = cls.epoch_ms(stamps.get(key))
                if at is not None and at >= since_ms:
                    marks[name] = min(marks.get(name, at), at)
        return marks

    @classmethod
    def message_type(cls, message: Mapping[str, object]) -> object:
        """A message's type (``ai``, ``tool``, ...), plain or from LangChain's constructor
        form, whose own ``type`` names the envelope."""
        if message.get("type") != "constructor":
            return message.get("type")
        path = message.get("id")
        kind = path[-1] if isinstance(path, list) and path else None
        if not isinstance(kind, str):
            return None
        kind = kind.removesuffix("Chunk").removesuffix("Message")
        return "ai" if kind == "AI" else kind.lower()

    @staticmethod
    def message_field(message: Mapping[str, object], name: str) -> object:
        """A field of a message as a run's state serialized it, plain or as LangChain's
        constructor form."""
        if name in message:
            return message[name]
        kwargs = message.get("kwargs")
        return kwargs.get(name) if isinstance(kwargs, Mapping) else None

    @classmethod
    def settle_in_background(cls, run: Mapping[str, object], *, failed: bool) -> None:
        """Settle a finished run's send without holding up the rest of its completion."""
        task = asyncio.create_task(cls.settle(run, failed=failed))
        cls._settling.add(task)
        task.add_done_callback(cls._settling.discard)

    @classmethod
    async def settle(cls, run: Mapping[str, object], *, failed: bool) -> None:
        """Time the send a finished run names, and close its trace; it never raises.

        Only the run the send started does: another that inherited the send's metadata
        would time it again from the same replies, and move the trace's end to its own.
        """
        try:
            timeline = cls.of_run(run)
        except Exception:
            logger.warning("Could not read a dashboard send's timing", exc_info=True)
            return
        thread_id = run.get("thread_id")
        if timeline is None or not isinstance(thread_id, str):
            return
        thread = await cls.thread_metadata(thread_id, timeline.run_id)
        runs = thread.get(cls.STARTED_RUNS_KEY) if thread is not None else None
        started = runs.get(str(timeline.received_at_ms)) if isinstance(runs, Mapping) else None
        if started is not None and started != timeline.run_id:
            return
        # A completion may be delivered again. The marks are reported once, as the thread
        # records them, and the trace is closed once, as its end shows; each is retried by
        # a later delivery when it did not happen the first time.
        if thread is not None and timeline.marks and not cls.timed_already(thread, timeline.run_id):
            timeline.report(thread_id=thread_id)
            await cls.mark_timed(thread_id, thread, timeline.run_id)
        if timeline.trace is not None and not await timeline.trace.ended():
            timeline.trace_stages()
            await timeline.trace.close(run, failed=failed)

    @classmethod
    def timed_already(
        cls, thread: Mapping[str, object], run_id: str, *, key: str | None = None
    ) -> bool:
        """Whether the thread records ``run_id``'s timing as reported already."""
        runs = thread.get(key or cls.TIMED_RUNS_KEY)
        return isinstance(runs, list) and run_id in runs

    @classmethod
    async def mark_timed(
        cls, thread_id: str, thread: Mapping[str, object], run_id: str, *, key: str | None = None
    ) -> None:
        """Record on the thread that ``run_id``'s timing is reported, among its latest."""
        from openswe.threads.store import THREADS

        key = key or cls.TIMED_RUNS_KEY
        runs = thread.get(key)
        kept: list[JsonValue] = (
            [r for r in runs if isinstance(r, str) and r != run_id]
            if isinstance(runs, list)
            else []
        )
        try:
            await THREADS.update(thread_id, {key: [*kept, run_id][-cls._TIMED_RUNS_KEPT :]})
        except Exception:
            logger.warning(
                "Could not record a run's timing as reported",
                extra={"agent_thread_id": thread_id, "run_id": run_id},
                exc_info=True,
            )

    @classmethod
    def started(
        cls, thread: Mapping[str, object], *, received_at_ms: int, run_id: str
    ) -> dict[str, JsonValue]:
        """The thread's record of the runs its sends started, with this send's run added."""
        runs = thread.get(cls.STARTED_RUNS_KEY)
        kept: dict[str, JsonValue] = (
            {k: v for k, v in runs.items() if isinstance(v, str)}
            if isinstance(runs, Mapping)
            else {}
        )
        kept[str(received_at_ms)] = run_id
        return dict(sorted(kept.items(), key=lambda item: int(item[0]))[-cls._STARTED_RUNS_KEPT :])

    @staticmethod
    def epoch_ms(value: object) -> int | None:
        """``value`` as epoch ms, when it is an integer."""
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @staticmethod
    async def thread_metadata(thread_id: str, run_id: str) -> Mapping[str, object] | None:
        """The thread's metadata, read once the run has ended; ``None`` when it cannot be."""
        from openswe.threads.store import THREADS

        try:
            thread = await THREADS.get(thread_id)
        except Exception:
            logger.warning(
                "Could not read the thread for its run timing",
                extra={"agent_thread_id": thread_id, "run_id": run_id},
                exc_info=True,
            )
            return None
        return thread.metadata if thread is not None else {}

    def seconds(self) -> dict[str, float]:
        """Each mark the run reached, in seconds after the send."""
        return {name: (at - self.received_at_ms) / 1000 for name, at in self.marks.items()}

    def trace_stages(self) -> None:
        """Span each stage of the send on its trace, even when the run gave no output."""
        if self.trace is None:
            return
        # The request that started the run traced ``commands`` itself, as it ran.
        stages = [
            *(stage for stage in self.stages() if stage[0] != "commands"),
            *self.first_reply_stage(),
        ]
        try:
            self.trace.record_stages(stages, run_id=self.run_id)
        except Exception:
            logger.warning("Failed to trace a dashboard send's stages", exc_info=True)

    def report(self, *, thread_id: str) -> None:
        """Record the marks the run reached to the histograms and the log; never raises."""
        run_id = self.run_id
        seconds = self.seconds()
        if not seconds:
            return
        try:
            self._record_histograms(seconds)
        except Exception:
            logger.warning("Failed to record dashboard run timing histograms", exc_info=True)
        logger.info(
            "Dashboard run timing",
            extra={
                "agent_thread_id": thread_id,
                "run_id": run_id,
                "dashboard_client": self.client.value,
                **{f"{name}_ms": round(value * 1000) for name, value in seconds.items()},
            },
        )

    def _record_histograms(self, seconds: Mapping[str, float]) -> None:
        self.record_latencies(
            {self._METRICS[name]: value for name, value in seconds.items()},
            attributes={
                "source": "dashboard",
                "client": self.client.value,
            },
        )

    @classmethod
    def record_latencies(
        cls, seconds: Mapping[str, float], *, attributes: Mapping[str, str]
    ) -> None:
        """Record each latency, in seconds, to the OTLP histogram its metric name names."""
        from langgraph_api.metrics_otlp import (
            METRIC_TIER_INFO,
            def_latency,
            get_otlp_metrics_reporter,
        )

        reporter = get_otlp_metrics_reporter()
        for name, value in seconds.items():
            metric = cls._definitions.get(name)
            if metric is None:
                metric = cls._definitions[name] = def_latency(name, METRIC_TIER_INFO)
            reporter.record_latency(metric, value, attributes=dict(attributes))

    def _points(self) -> list[tuple[str, int]]:
        """The send's points in the order they happen, none earlier than the one before.

        Another host's clock can trail the backend's own; a later point never draws a
        span that ends before it starts.
        """
        points = [
            *([("submitted", self.submitted_at_ms)] if self.submitted_at_ms is not None else []),
            ("received", self.received_at_ms),
            *([("accepted", self.accepted_at_ms)] if self.accepted_at_ms is not None else []),
            *((name, self.marks[name]) for name in self._STAGES if name in self.marks),
        ]
        ordered: list[tuple[str, int]] = []
        for name, at in points:
            ordered.append((name, max(at, ordered[-1][1]) if ordered else at))
        return ordered

    def first_reply_stage(self) -> list[tuple[str, int, int]]:
        """The span from the send to the first text its sender saw, once there was some."""
        first_text = self.marks.get("ttft")
        if first_text is None:
            return []
        return [
            (
                DashboardSendTrace.FIRST_REPLY,
                self.received_at_ms,
                max(first_text, self.received_at_ms),
            )
        ]

    def stages(self) -> list[tuple[str, int, int]]:
        """Each stage the send went through, as ``(span name, began, ended)`` in epoch ms."""
        return [
            (self._STAGES[name], began, ended)
            for (_, began), (name, ended) in pairwise(self._points())
        ]
