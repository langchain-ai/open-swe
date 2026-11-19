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


def _install(monkeypatch: pytest.MonkeyPatch) -> object:
    """Install the processor on a stand-in for Datadog's tracer, and return it."""
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
    (processor,) = processors
    return processor


def test_datadogs_spans_of_a_runs_work_are_posted_under_it_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor = _install(monkeypatch)
    posted: list[dict[str, object]] = []
    posting_threads: set[str] = set()

    def create_run(**run: object) -> None:
        posted.append(run)
        posting_threads.add(threading.current_thread().name)

    run = RunTree(name="build_run", ls_client=SimpleNamespace(create_run=create_run))

    with DatadogSpans.under(run):
        pass
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


def test_each_span_is_posted_under_its_parent_however_late_the_parent_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor = _install(monkeypatch)
    posted: list[dict[str, object]] = []
    run = RunTree(
        name="start_run", ls_client=SimpleNamespace(create_run=lambda **r: posted.append(r))
    )

    with DatadogSpans.under(run):
        pass
    # A send's trace holds thousands of spans, which arrive in chunks, deepest first.
    renders = [_DatadogSpan(100 + i, 11, "jinja2.render") for i in range(500)]
    processor.process_trace([*renders, _DatadogSpan(13, 12, "postgres.query")])  # type: ignore[attr-defined]
    processor.process_trace([_DatadogSpan(12, 11, "http.request")])  # type: ignore[attr-defined]
    # Outside the run: it waits, and is never posted.
    processor.process_trace([_DatadogSpan(14, 2, "redis.command")])  # type: ignore[attr-defined]
    processor.process_trace(  # type: ignore[attr-defined]
        [_DatadogSpan(11, 10, "catalog"), _DatadogSpan(10, 1, "langsmith.run")]
    )

    DatadogSpans.drain(timeout=5)
    by_name: dict[str, list[dict[str, object]]] = {}
    for posted_run in posted:
        by_name.setdefault(str(posted_run["name"]), []).append(posted_run)
    assert set(by_name) == {"catalog", "http.request", "postgres.query", "jinja2.render"}
    (catalog,), (request,), (query,) = (
        by_name["catalog"],
        by_name["http.request"],
        by_name["postgres.query"],
    )
    assert catalog["parent_run_id"] == run.id
    assert request["parent_run_id"] == catalog["id"]
    assert query["parent_run_id"] == request["id"]
    assert len(by_name["jinja2.render"]) == 500
    assert {r["parent_run_id"] for r in by_name["jinja2.render"]} == {catalog["id"]}


def test_requests_ddtrace_left_without_a_path_are_named_after_their_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ddtrace's last pass leaves this app's requests as ``POST ``; APM can't tell them apart."""
    processor = _install(monkeypatch)
    route = "/dashboard/api/repos/{owner}/{repo}/pulls/{number}/human-review"
    unnamed = _DatadogSpan(
        1, None, "fastapi.request", **{"http.method": "POST", "http.route": route}
    )
    unnamed.resource = "POST "
    named = _DatadogSpan(2, None, "fastapi.request", **{"http.method": "GET", "http.route": "/ok"})
    named.resource = "GET /threads/search"

    processor.process_trace([unnamed, named])  # type: ignore[attr-defined]

    assert unnamed.resource == f"POST {route}"
    assert named.resource == "GET /threads/search"
