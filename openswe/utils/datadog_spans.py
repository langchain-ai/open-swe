"""Datadog's spans of a LangSmith run's work, posted into that run's trace.

Where Datadog traces this backend, ddtrace spans each library call (asyncpg, httpx,
redis, gRPC, Jinja2 and more). ``DatadogSpans.under(run)`` opens a ddtrace marker span
for a LangSmith run, so the calls made in its block are the marker's descendants. A
ddtrace trace processor posts each of them, as it finishes, under the run, nested as
Datadog nests them. Children finish before their parents, so a span waits until its
parent's run is known. Without ddtrace, nothing is traced, and tracing never raises
into the work it traces.
"""

import logging
import threading
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


class DatadogSpans:
    """The ddtrace trace processor posting Datadog's spans under LangSmith runs."""

    _installed: ClassVar[bool] = False
    _lock: ClassVar[threading.Lock] = threading.Lock()
    # The LangSmith run each marker or posted span is, by Datadog trace and span id.
    _runs: ClassVar[dict[tuple[int, int], RunTree]] = {}
    # Finished spans waiting for their parent's run, by Datadog trace id.
    _waiting: ClassVar[dict[int, dict[int, Span]]] = {}
    # How many markers of each Datadog trace have not finished yet.
    _open: ClassVar[dict[int, int]] = {}

    @classmethod
    def install(cls) -> None:
        """Have Datadog's tracer, when it traces this process, hand its spans over."""
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
        cls._installed = True

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
                    if span.trace_id in cls._waiting and span.name != _MARKER:
                        cls._waiting[span.trace_id][span.span_id] = span
                for trace_id in {span.trace_id for span in trace}:
                    cls._post_ready(trace_id, cls._waiting.get(trace_id, {}))
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
    def _post_ready(cls, trace_id: int, waiting: dict[int, Span]) -> None:
        ready = [span for span in waiting.values() if (trace_id, span.parent_id) in cls._runs]
        while ready:
            span = ready.pop()
            del waiting[span.span_id]
            parent = cls._runs[trace_id, span.parent_id]
            cls._runs[trace_id, span.span_id] = cls._post(span, parent)
            ready.extend(child for child in waiting.values() if child.parent_id == span.span_id)

    @staticmethod
    def _post(span: Span, parent: RunTree) -> RunTree:
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
        run.post()
        return run
