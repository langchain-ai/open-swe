"""Runs are read from the project the MDA deployment traces them into."""

import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest
from langsmith.run_trees import RunTree

from openswe.utils.datadog_spans import DatadogSpans
from openswe.utils.tracing import tracing_project


def test_the_mda_deployments_project_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_PROJECT", "backend")
    monkeypatch.setenv("MDA_LANGSMITH_PROJECT", "open-swe-mda")

    assert tracing_project() == "open-swe-mda"


def test_unset_it_is_this_backends_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_PROJECT", "backend")
    monkeypatch.delenv("MDA_LANGSMITH_PROJECT", raising=False)

    assert tracing_project() == "backend"


class _DatadogSpan:
    """A span as Datadog's tracer hands it over, finished."""

    def __init__(self, span_id: int, parent_id: int | None, name: str, **tags: str) -> None:
        self.span_id, self.parent_id, self.trace_id = span_id, parent_id, 1
        self.name = self.resource = name
        self.service, self.start_ns, self.duration_ns, self.error = "openswe", 0, 1_000, 0
        self.tags = tags

    def get_tag(self, key: str) -> str | None:
        return self.tags.get(key)

    def __enter__(self) -> _DatadogSpan:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_datadogs_spans_of_a_runs_work_are_posted_under_it_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processors: list[object] = []

    class TraceFilter:
        pass

    tracer = SimpleNamespace(
        enabled=True,
        configure=lambda trace_processors: processors.extend(trace_processors),
        trace=lambda name, resource: _DatadogSpan(10, 1, name),
    )
    module = ModuleType("ddtrace.trace")
    module.tracer, module.TraceFilter = tracer, TraceFilter  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ddtrace", ModuleType("ddtrace"))
    monkeypatch.setitem(sys.modules, "ddtrace.trace", module)
    monkeypatch.setattr(DatadogSpans, "_installed", False)
    DatadogSpans.install()
    posted: list[dict[str, object]] = []
    posting_threads: set[str] = set()

    def create_run(**run: object) -> None:
        posted.append(run)
        posting_threads.add(threading.current_thread().name)

    run = RunTree(name="build_run", ls_client=SimpleNamespace(create_run=create_run))

    with DatadogSpans.under(run):
        pass
    (processor,) = processors
    # Children finish first: the query waits for the request it was made in.
    processor.process_trace([_DatadogSpan(12, 11, "postgres.query")])  # type: ignore[attr-defined]
    processor.process_trace(  # type: ignore[attr-defined]
        [
            _DatadogSpan(11, 10, "http.request", **{"http.url": "https://x.test/a?token=s"}),
            _DatadogSpan(10, 1, "langsmith.run"),
            _DatadogSpan(1, None, "fastapi.request"),
        ]
    )

    DatadogSpans.drain(timeout=5)
    names = {str(r["name"]): r for r in posted}
    assert set(names) == {"http.request https://x.test/a", "postgres.query"}
    assert names["http.request https://x.test/a"]["parent_run_id"] == run.id
    assert names["postgres.query"]["parent_run_id"] == names["http.request https://x.test/a"]["id"]
    assert threading.current_thread().name not in posting_threads
