"""Unit tests for the server-backed enqueue proxy: POST/GET /threads/{id}/runs."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import HTTPException
from langgraph_sdk.errors import ConflictError, NotFoundError

from agent.threads import proxy as thread_proxy
from tests.conftest import patch_thread_module

DASHBOARD_METADATA = {"source": "dashboard", "participant_logins": {}}


def _not_found() -> NotFoundError:
    response = httpx2.Response(404, request=httpx2.Request("GET", "http://test"))
    return NotFoundError("not found", response=response, body=None)


class _Threads:
    """Fake threads client with a real create-lock backing the enqueue lock."""

    def __init__(self, *, thread: dict[str, Any] | None, not_found_first: int = 0) -> None:
        self._thread = thread
        self._not_found_first = not_found_first
        self.get_calls = 0
        self.updates: list[dict[str, Any]] = []
        self._locked: set[str] = set()
        self._lock_gate = asyncio.Lock()

    async def get(self, thread_id: str) -> dict[str, Any]:
        self.get_calls += 1
        if self.get_calls <= self._not_found_first or self._thread is None:
            raise _not_found()
        return self._thread

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.updates.append(metadata)

    async def get_state(self, thread_id: str) -> dict[str, Any]:
        return {"values": {"messages": []}}

    async def create(self, *, thread_id: str, if_exists: str, ttl: int) -> None:
        assert if_exists == "raise"
        async with self._lock_gate:
            if thread_id in self._locked:
                response = httpx2.Response(409, request=httpx2.Request("POST", "http://test"))
                raise ConflictError("already locked", response=response, body=None)
            self._locked.add(thread_id)

    async def delete(self, thread_id: str) -> None:
        self._locked.discard(thread_id)


class _Runs:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []

    async def create(self, thread_id: str, assistant_id: str, **kwargs: Any) -> dict[str, Any]:
        self.create_calls.append({"thread_id": thread_id, "assistant_id": assistant_id, **kwargs})
        return {"run_id": f"run-{len(self.create_calls)}"}


class _Client:
    def __init__(self, *, thread: dict[str, Any] | None, not_found_first: int = 0) -> None:
        self.threads = _Threads(thread=thread, not_found_first=not_found_first)
        self.runs = _Runs()


def _patch_enqueue_deps(monkeypatch: pytest.MonkeyPatch, client: _Client) -> None:
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)
    patch_thread_module(monkeypatch, "get_profile", AsyncMock(return_value={}))
    patch_thread_module(monkeypatch, "_ensure_dashboard_github_token", AsyncMock())
    patch_thread_module(
        monkeypatch, "resolve_run_email", AsyncMock(return_value="octocat@example.com")
    )


def _body(**overrides: Any) -> bytes:
    payload: dict[str, Any] = {
        "input": {"messages": [{"type": "human", "content": "hi", "id": "m1"}]},
        "config": {"configurable": {}},
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


async def test_enqueue_forces_server_side_multitask_strategy_ignoring_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(thread={"thread_id": "tid", "metadata": DASHBOARD_METADATA})
    _patch_enqueue_deps(monkeypatch, client)

    await thread_proxy.proxy_dashboard_thread_run_enqueue(
        "tid", "octocat", _body(multitask_strategy="interrupt")
    )

    assert client.runs.create_calls[0]["multitask_strategy"] == "enqueue"


async def test_enqueue_preserves_input_and_config_sibling_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(thread={"thread_id": "tid", "metadata": DASHBOARD_METADATA})
    _patch_enqueue_deps(monkeypatch, client)

    await thread_proxy.proxy_dashboard_thread_run_enqueue(
        "tid",
        "octocat",
        _body(
            input={
                "messages": [{"type": "human", "content": "hi", "id": "m1"}],
                "files": {"/SKILL.md": {"content": "do the thing", "encoding": "utf-8"}},
            },
            config={"configurable": {}, "tags": ["from-skill"]},
        ),
    )

    call = client.runs.create_calls[0]
    assert call["input"]["files"] == {"/SKILL.md": {"content": "do the thing", "encoding": "utf-8"}}
    assert call["config"]["tags"] == ["from-skill"]


async def test_enqueue_persists_latest_run_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client(thread={"thread_id": "tid", "metadata": DASHBOARD_METADATA})
    _patch_enqueue_deps(monkeypatch, client)

    run = await thread_proxy.proxy_dashboard_thread_run_enqueue("tid", "octocat", _body())

    assert run["run_id"] == "run-1"
    latest = client.threads.updates[-1]
    assert latest["latest_run_id"] == "run-1"
    assert latest["latest_run_status"] == "pending"


async def test_enqueue_survives_slack_notify_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    metadata = {
        **DASHBOARD_METADATA,
        "source": "slack",
        "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}},
    }
    client = _Client(thread={"thread_id": "tid", "metadata": metadata})
    _patch_enqueue_deps(monkeypatch, client)
    patch_thread_module(
        monkeypatch, "_notify_slack_web_handoff", AsyncMock(side_effect=RuntimeError("slack down"))
    )

    run = await thread_proxy.proxy_dashboard_thread_run_enqueue("tid", "octocat", _body())

    assert run["run_id"] == "run-1"


async def test_enqueue_404s_when_thread_never_appears(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client(thread=None)
    _patch_enqueue_deps(monkeypatch, client)

    with pytest.raises(HTTPException) as exc_info:
        await thread_proxy.proxy_dashboard_thread_run_enqueue("tid", "octocat", _body())

    assert exc_info.value.status_code == 404


async def test_enqueue_tolerates_a_brief_create_race_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thread still being created by a concurrent run.start isn't a 404."""
    monkeypatch.setattr(thread_proxy.asyncio, "sleep", AsyncMock())
    client = _Client(thread={"thread_id": "tid", "metadata": DASHBOARD_METADATA}, not_found_first=2)
    _patch_enqueue_deps(monkeypatch, client)

    run = await thread_proxy.proxy_dashboard_thread_run_enqueue("tid", "octocat", _body())

    assert run["run_id"] == "run-1"
    assert client.threads.get_calls == 3


async def test_get_thread_tolerating_create_race_returns_none_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(thread_proxy.asyncio, "sleep", AsyncMock())
    client = _Client(thread={"thread_id": "tid"}, not_found_first=999)

    result = await thread_proxy._get_thread_tolerating_create_race(client, "tid")

    assert result is None
    assert client.threads.get_calls == 4  # (0.0, 0.15, 0.3, 0.6) attempts


async def test_get_thread_tolerating_create_race_propagates_non_not_found_errors() -> None:
    """A real outage/auth failure must surface as itself, not become a 404."""

    class _FailingThreads:
        async def get(self, thread_id: str) -> dict[str, Any]:
            raise RuntimeError("backend is down")

    class _FailingClient:
        threads = _FailingThreads()

    with pytest.raises(RuntimeError, match="backend is down"):
        await thread_proxy._get_thread_tolerating_create_race(_FailingClient(), "tid")


async def test_concurrent_enqueues_on_the_same_thread_are_serialized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock must not let two racing enqueues interleave their dispatch."""
    client = _Client(thread={"thread_id": "tid", "metadata": DASHBOARD_METADATA})
    _patch_enqueue_deps(monkeypatch, client)

    order: list[str] = []
    real_create = client.runs.create

    async def tracking_create(thread_id: str, assistant_id: str, **kwargs: Any) -> dict[str, Any]:
        order.append("start")
        await asyncio.sleep(0.05)
        order.append("end")
        return await real_create(thread_id, assistant_id, **kwargs)

    client.runs.create = tracking_create

    await asyncio.gather(
        thread_proxy.proxy_dashboard_thread_run_enqueue("tid", "octocat", _body()),
        thread_proxy.proxy_dashboard_thread_run_enqueue("tid", "octocat", _body()),
    )

    # Interleaved (unserialized) would produce ["start", "start", "end", "end"].
    assert order == ["start", "end", "start", "end"]
