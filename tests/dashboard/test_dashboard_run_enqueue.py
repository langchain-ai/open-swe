"""Unit tests for the "stream"-kind queue proxy: POST/GET /threads/{id}/runs."""

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import HTTPException
from langgraph_sdk.errors import NotFoundError

from agent.threads import proxy as thread_proxy
from tests.conftest import patch_thread_module

DASHBOARD_METADATA = {
    "source": "dashboard",
    "visibility": "public",
    "owner_login": "octocat",
}


def _not_found() -> NotFoundError:
    response = httpx2.Response(404, request=httpx2.Request("GET", "http://test"))
    return NotFoundError("not found", response=response, body=None)


class _FakeThreads:
    def __init__(self, *, thread: dict[str, Any] | None, not_found_first: int = 0) -> None:
        self._thread = thread
        self._not_found_first = not_found_first
        self.get_calls = 0

    async def get(self, thread_id: str) -> dict[str, Any]:
        self.get_calls += 1
        if self.get_calls <= self._not_found_first:
            raise _not_found()
        if self._thread is None:
            raise _not_found()
        return self._thread


class _FakeRuns:
    def __init__(self) -> None:
        self.get_calls: list[tuple[str, str]] = []
        self.get_result: dict[str, Any] = {"run_id": "run-1", "status": "pending"}

    async def get(self, thread_id: str, run_id: str) -> dict[str, Any]:
        self.get_calls.append((thread_id, run_id))
        return self.get_result


class _FakeClient:
    def __init__(self, threads: _FakeThreads, runs: _FakeRuns) -> None:
        self.threads = threads
        self.runs = runs


def _body(**overrides: Any) -> bytes:
    payload: dict[str, Any] = {
        "input": {"messages": [{"id": "m1", "type": "human", "content": "hi"}]},
        "config": {"configurable": {}},
        "metadata": {},
        # Never trusted: the endpoint always enqueues regardless.
        "multitask_strategy": "interrupt",
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


async def test_runs_list_forwards_pagination_and_select(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200
        content = b"[]"
        headers = {"content-type": "application/json"}

    class FakeAsyncClient:
        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, *, headers: dict[str, str], params: Any) -> FakeResponse:
            captured["url"] = url
            captured["params"] = params
            return FakeResponse()

    monkeypatch.setattr(
        thread_proxy, "_readable_thread_metadata", AsyncMock(return_value=DASHBOARD_METADATA)
    )
    monkeypatch.setattr(thread_proxy.httpx2, "AsyncClient", lambda **_: FakeAsyncClient())

    status_code, content, media_type = await thread_proxy.proxy_dashboard_thread_runs_list(
        "tid",
        "octocat",
        limit=5,
        offset=10,
        status="pending",
        select=["kwargs", "created_at"],
    )

    assert status_code == 200
    assert content == b"[]"
    assert media_type == "application/json"
    assert captured["params"] == [
        ("limit", "5"),
        ("offset", "10"),
        ("status", "pending"),
        ("select", "kwargs"),
        ("select", "created_at"),
    ]


async def test_get_thread_tolerating_create_race_succeeds_immediately() -> None:
    threads = _FakeThreads(thread={"thread_id": "tid"})
    client = _FakeClient(threads, _FakeRuns())

    result = await thread_proxy._get_thread_tolerating_create_race(client, "tid")

    assert result == {"thread_id": "tid"}
    assert threads.get_calls == 1


async def test_get_thread_tolerating_create_race_retries_then_succeeds() -> None:
    threads = _FakeThreads(thread={"thread_id": "tid"}, not_found_first=2)
    client = _FakeClient(threads, _FakeRuns())

    result = await thread_proxy._get_thread_tolerating_create_race(client, "tid")

    assert result == {"thread_id": "tid"}
    assert threads.get_calls == 3  # (0.0, 0.15) attempts before the third succeeds


async def test_get_thread_tolerating_create_race_exhausts_retries() -> None:
    threads = _FakeThreads(thread=None)
    client = _FakeClient(threads, _FakeRuns())

    result = await thread_proxy._get_thread_tolerating_create_race(client, "tid")

    assert result is None
    assert threads.get_calls == 4  # (0.0, 0.15, 0.3, 0.6) attempts


async def test_get_thread_tolerating_create_race_propagates_other_errors() -> None:
    class BrokenThreads:
        async def get(self, thread_id: str) -> dict[str, Any]:
            raise RuntimeError("outage")

    client = _FakeClient(BrokenThreads(), _FakeRuns())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        await thread_proxy._get_thread_tolerating_create_race(client, "tid")


async def test_enqueue_404s_when_thread_never_appears(monkeypatch) -> None:
    threads = _FakeThreads(thread=None)
    client = _FakeClient(threads, _FakeRuns())
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    with pytest.raises(HTTPException) as exc_info:
        await thread_proxy.proxy_dashboard_thread_run_enqueue("tid", "octocat", _body())
    assert exc_info.value.status_code == 404


async def test_enqueue_requires_posting_access(monkeypatch) -> None:
    threads = _FakeThreads(
        thread={
            "metadata": {
                **DASHBOARD_METADATA,
                "visibility": "private",
                "owner_login": "someone-else",
            }
        }
    )
    client = _FakeClient(threads, _FakeRuns())
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    with pytest.raises(HTTPException) as exc_info:
        await thread_proxy.proxy_dashboard_thread_run_enqueue("tid", "octocat", _body())
    assert exc_info.value.status_code == 404


async def test_enqueue_reshapes_body_into_a_run_start_command_and_delegates(monkeypatch) -> None:
    threads = _FakeThreads(thread={"metadata": DASHBOARD_METADATA})
    runs = _FakeRuns()
    client = _FakeClient(threads, runs)
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    captured: dict[str, Any] = {}

    async def fake_queue_follow_up_run(
        thread_id: str,
        login: str,
        command: dict[str, Any],
        *,
        metadata: dict[str, Any],
        email: str | None,
    ) -> dict[str, Any]:
        captured["thread_id"] = thread_id
        captured["login"] = login
        captured["command"] = command
        captured["metadata"] = metadata
        return {"id": None, "type": "success", "result": {"run_id": "run-1", "queued": True}}

    monkeypatch.setattr(thread_proxy, "queue_follow_up_run", fake_queue_follow_up_run)

    result = await thread_proxy.proxy_dashboard_thread_run_enqueue(
        "tid", "octocat", _body(), email="octocat@example.com"
    )

    # The client's own multitask_strategy is never forwarded to the command.
    assert captured["command"] == {
        "method": "run.start",
        "params": {
            "input": {"messages": [{"id": "m1", "type": "human", "content": "hi"}]},
            "config": {"configurable": {}},
            "metadata": {},
        },
    }
    assert captured["thread_id"] == "tid"
    assert captured["login"] == "octocat"
    # The raw runs REST caller gets a `Run` object back, not the commands
    # protocol's `{id, type, result}` envelope.
    assert result == runs.get_result
    assert runs.get_calls == [("tid", "run-1")]


async def test_enqueue_502s_when_queue_follow_up_run_returns_no_run_id(monkeypatch) -> None:
    threads = _FakeThreads(thread={"metadata": DASHBOARD_METADATA})
    client = _FakeClient(threads, _FakeRuns())
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    async def fake_queue_follow_up_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"id": None, "type": "error", "result": {}}

    monkeypatch.setattr(thread_proxy, "queue_follow_up_run", fake_queue_follow_up_run)

    with pytest.raises(HTTPException) as exc_info:
        await thread_proxy.proxy_dashboard_thread_run_enqueue("tid", "octocat", _body())
    assert exc_info.value.status_code == 502


async def test_enqueue_rejects_a_non_json_body(monkeypatch) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await thread_proxy.proxy_dashboard_thread_run_enqueue("tid", "octocat", b"not json")
    assert exc_info.value.status_code == 400


async def test_enqueue_tolerates_a_brief_create_race_then_succeeds(monkeypatch) -> None:
    threads = _FakeThreads(thread={"metadata": DASHBOARD_METADATA}, not_found_first=2)
    runs = _FakeRuns()
    client = _FakeClient(threads, runs)
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    async def fake_queue_follow_up_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"id": None, "type": "success", "result": {"run_id": "run-1", "queued": True}}

    monkeypatch.setattr(thread_proxy, "queue_follow_up_run", fake_queue_follow_up_run)

    result = await thread_proxy.proxy_dashboard_thread_run_enqueue("tid", "octocat", _body())
    assert result == runs.get_result
