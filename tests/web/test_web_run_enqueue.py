"""Unit tests for the "stream"-kind queue proxy: POST/GET /threads/{id}/runs."""

import json
from typing import Any

import httpx2
import pytest
from fastapi import HTTPException
from langgraph_sdk.errors import NotFoundError

from openswe.threads import proxy as thread_proxy
from tests.conftest import patch_thread_module

WEB_METADATA = {
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


async def test_enqueue_requires_posting_access(monkeypatch) -> None:
    threads = _FakeThreads(
        thread={
            "metadata": {
                **WEB_METADATA,
                "visibility": "private",
                "owner_login": "someone-else",
            }
        }
    )
    client = _FakeClient(threads, _FakeRuns())
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    with pytest.raises(HTTPException) as exc_info:
        await thread_proxy.proxy_web_thread_run_enqueue("tid", "octocat", _body())
    assert exc_info.value.status_code == 404


async def test_enqueue_502s_when_queue_follow_up_run_returns_no_run_id(monkeypatch) -> None:
    threads = _FakeThreads(thread={"metadata": WEB_METADATA})
    client = _FakeClient(threads, _FakeRuns())
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    async def fake_queue_follow_up_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"id": None, "type": "error", "result": {}}

    monkeypatch.setattr(thread_proxy, "queue_follow_up_run", fake_queue_follow_up_run)

    with pytest.raises(HTTPException) as exc_info:
        await thread_proxy.proxy_web_thread_run_enqueue("tid", "octocat", _body())
    assert exc_info.value.status_code == 502


async def test_enqueue_tolerates_a_brief_create_race_then_succeeds(monkeypatch) -> None:
    threads = _FakeThreads(thread={"metadata": WEB_METADATA}, not_found_first=2)
    runs = _FakeRuns()
    client = _FakeClient(threads, runs)
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    async def fake_queue_follow_up_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"id": None, "type": "success", "result": {"run_id": "run-1", "queued": True}}

    monkeypatch.setattr(thread_proxy, "queue_follow_up_run", fake_queue_follow_up_run)

    result = await thread_proxy.proxy_web_thread_run_enqueue("tid", "octocat", _body())
    assert result == runs.get_result
