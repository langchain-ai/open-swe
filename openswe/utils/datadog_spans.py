"""Datadog's spans of a LangSmith run's work, posted into that run's trace.

Where Datadog traces this backend, ddtrace spans each library call (asyncpg, httpx,
redis, gRPC, Jinja2 and more). ``DatadogSpans.under(run)`` opens a ddtrace marker span
for a LangSmith run, so the calls made in its block are the marker's descendants. A
ddtrace trace processor queues each of them, as it finishes, to be posted under the
run, nested as Datadog nests them. Children finish before their parents, so a span
waits until its parent's run is known. Without ddtrace, nothing is traced, and tracing
never raises into the work it traces.
"""

import logging
import queue
import threading
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Final

from langsmith.run_trees import RunTree

if TYPE_CHECKING:
    from ddtrace.trace import Span

logger = logging.getLogger(__name__)

_MARKER: Final = "langsmith.run"
# Past this many runs waiting to be posted, a span's run is dropped rather than
# letting a stalled LangSmith upload grow memory.
_POST_BACKLOG: Final = 10_000


class DatadogSpans:
    """The ddtrace trace processor posting Datadog's spans under LangSmith runs."""

    _installed: ClassVar[bool] = False
    _lock: ClassVar[threading.Lock] = threading.Lock()
    # The LangSmith run each marker or posted span is, by Datadog trace and span id.
    _runs: ClassVar[dict[tuple[int, int], RunTree]] = {}
    # Finished spans waiting for their parent's run, by Datadog trace id and then the
    # parent's span id. A send's trace holds thousands of spans, so a span's waiting
    # children are found by their parent, never by scanning every span that waits.
    _waiting: ClassVar[dict[int, dict[int | None, list[Span]]]] = {}
    # How many markers of each Datadog trace have not finished yet.
    _open: ClassVar[dict[int, int]] = {}
    # Posting a run writes it into the LangSmith client's compressed buffer under a
    # lock its upload thread also takes, so it is done on a thread of its own: on
    # the event loop it held every request on the process for a second at a time.
    _posts: ClassVar[queue.Queue[RunTree]] = queue.Queue(_POST_BACKLOG)

    @classmethod
    def install(cls) -> None:
        """Have Datadog's tracer, when it traces this process, hand its spans over."""
        if cls._installed:
            return
        try:
            from ddtrace.trace import TraceFilter, tracer
        except ImportError:
            return
        if not tracer.enabled:
            return

        class _Processor(TraceFilter):
            def process_trace(self, trace: list[Span]) -> list[Span]:
                cls.process(trace)
                return trace

        # The Agent Server registers no trace processors of its own.
        tracer.configure(trace_processors=[_Processor()])
        threading.Thread(target=cls._post_queued, name="datadog-spans", daemon=True).start()
        cls._installed = True

    @classmethod
    def drain(cls, timeout: float) -> None:
        """Wait up to ``timeout`` seconds for the spans already queued to be posted."""
        deadline = time.monotonic() + timeout
        while cls._posts.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.05)

    @classmethod
    def _post_queued(cls) -> None:
        while True:
            run = cls._posts.get()
            try:
                run.post()
            except Exception:
                logger.debug("Could not post a Datadog span to LangSmith", exc_info=True)
            finally:
                cls._posts.task_done()

    @classmethod
    @contextmanager
    def under(cls, run: RunTree | None) -> Generator[None]:
        """Post the Datadog spans of the calls made in this block under ``run``."""
        if run is None or not cls._installed:
            yield
            return
        from ddtrace.trace import tracer

        with tracer.trace(_MARKER, resource=run.name) as marker:
            with cls._lock:
                cls._runs[marker.trace_id, marker.span_id] = run
                cls._waiting.setdefault(marker.trace_id, {})
                cls._open[marker.trace_id] = cls._open.get(marker.trace_id, 0) + 1
            yield

    @classmethod
    def process(cls, trace: list[Span]) -> None:
        with cls._lock:
            try:
                for span in trace:
                    waiting = cls._waiting.get(span.trace_id)
                    if waiting is not None and span.name != _MARKER:
                        cls._post_ready(span, waiting)
            except Exception:
                logger.debug("Could not post Datadog's spans to LangSmith", exc_info=True)
            for span in trace:
                # A marker finishes after its block's spans: once a trace's last marker
                # has, what still waits has no run to go under.
                if span.name == _MARKER and span.trace_id in cls._open:
                    cls._open[span.trace_id] -= 1
                    if cls._open[span.trace_id] <= 0:
                        del cls._open[span.trace_id]
                        cls._waiting.pop(span.trace_id, None)
                        cls._runs = {k: r for k, r in cls._runs.items() if k[0] != span.trace_id}

    @classmethod
    def _post_ready(cls, span: Span, waiting: dict[int | None, list[Span]]) -> None:
        """Post ``span`` once its parent's run is known, and then the children that waited
        for it; until then it waits for its parent."""
        trace_id = span.trace_id
        if (trace_id, span.parent_id) not in cls._runs:
            waiting.setdefault(span.parent_id, []).append(span)
            return
        ready = [span]
        while ready:
            span = ready.pop()
            run = cls._post(span, cls._runs[trace_id, span.parent_id])
            if run is None:
                # Dropped: its children wait for a run that never comes, and go with the trace.
                continue
            cls._runs[trace_id, span.span_id] = run
            ready.extend(waiting.pop(span.span_id, ()))

    @classmethod
    def _post(cls, span: Span, parent: RunTree) -> RunTree | None:
        if cls._posts.full():
            logger.debug("Dropped a Datadog span: its LangSmith posts are backed up")
            return None
        name = f"{span.name} {span.resource}" if span.resource != span.name else span.name
        if url := span.get_tag("http.url"):
            # Without the query string, which can carry credentials.
            name = f"{span.get_tag('http.method') or span.name} {str(url).split('?', 1)[0]}"
        run = parent.create_child(
            " ".join(name.split())[:200],
            run_id=uuid.uuid5(uuid.UUID(str(parent.trace_id)), str(span.span_id)),
            start_time=datetime.fromtimestamp(span.start_ns / 1e9, UTC),
            end_time=datetime.fromtimestamp((span.start_ns + (span.duration_ns or 0)) / 1e9, UTC),
            error=span.get_tag("error.message") or "error" if span.error else None,
            extra={"metadata": {"datadog_service": span.service, "datadog_name": span.name}},
        )
        cls._posts.put_nowait(run)
        return run
