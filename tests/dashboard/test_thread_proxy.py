"""Who may reach the LangGraph platform through the dashboard, and with what.

The command/history/stream endpoints are thin proxies, so what matters is the
authorization decision taken before the hop and the request that comes out of
it -- URL, headers, body. ``FakeHttpx`` records the latter.
"""

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from support.httpx_fakes import FakeHttpx
from support.langgraph_fakes import FakeLangGraphClient

from agent.dashboard import authz
from agent.dashboard.threads import proxy as thread_proxy
from agent.dashboard.threads import runs as thread_runs
from agent.dashboard.ttft import AssistantTextObservation

# The cap the history proxy clamps an undirected (no cursor, no filter) read to.
_DISCOVERY_HISTORY_LIMIT = 5


def _install_client(monkeypatch, **kwargs: Any) -> FakeLangGraphClient:
    client = FakeLangGraphClient(**kwargs)
    monkeypatch.setattr(authz, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_runs, "langgraph_client", lambda: client)
    return client


def _install_proxy(monkeypatch, **kwargs: Any) -> FakeHttpx:
    proxy = FakeHttpx(**kwargs)
    monkeypatch.setattr(thread_proxy.httpx, "AsyncClient", proxy.client)
    return proxy


def _admin_thread_metadata() -> dict[str, object]:
    return {
        "source": "dashboard",
        "github_login": "workspace-admin",
        "admin_thread": True,
    }


async def test_commands_lazily_create_a_missing_thread_only_for_run_start(monkeypatch) -> None:
    # No thread is seeded, so every ``get`` 404s the way the platform would.
    _install_client(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        await thread_runs.proxy_dashboard_thread_commands(
            "ghost", "octocat", b'{"method": "run.cancel"}'
        )
    assert exc_info.value.status_code == 404


async def test_commands_reject_non_object_body(monkeypatch) -> None:
    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "octocat"})

    with pytest.raises(HTTPException) as exc_info:
        await thread_runs.proxy_dashboard_thread_commands("tid", "octocat", b"[]")

    assert exc_info.value.status_code == 400


async def test_commands_other_than_run_start_stay_owner_only(monkeypatch) -> None:
    """Non-owners may only post via the attributed run.start path; other write
    commands (e.g. input.respond) carry unattributed input and stay owner-only."""

    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "owner"})

    with pytest.raises(HTTPException) as exc_info:
        await thread_runs.proxy_dashboard_thread_commands(
            "tid", "intruder", b'{"method": "input.respond"}'
        )
    assert exc_info.value.status_code == 404


async def test_commands_reject_non_admin_on_admin_thread(monkeypatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin")
    _install_client(monkeypatch, thread_metadata=_admin_thread_metadata())

    with pytest.raises(HTTPException) as exc_info:
        await thread_runs.proxy_dashboard_thread_commands(
            "tid", "teammate", b'{"method": "run.start"}'
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "only admins can send messages in admin threads"


async def test_commands_preserve_admin_writes_and_owner_reads(monkeypatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin,another-admin")
    _install_client(monkeypatch, thread_metadata=_admin_thread_metadata())
    proxy = _install_proxy(monkeypatch, headers={})

    status_code, _, _ = await thread_runs.proxy_dashboard_thread_commands(
        "tid", "another-admin", b'{"method": "input.respond"}'
    )

    assert status_code == 200

    monkeypatch.setenv("CONFIGURED_ADMINS", "another-admin")
    status_code, _, _ = await thread_runs.proxy_dashboard_thread_commands(
        "tid", "workspace-admin", b'{"method": "agent.getTree"}'
    )

    assert status_code == 200
    assert [request.content for request in proxy.requests] == [
        b'{"method": "input.respond"}',
        b'{"method": "agent.getTree"}',
    ]


async def test_run_cancel_enforces_thread_ownership(monkeypatch) -> None:
    """Cancelling a run still requires thread ownership (it is not "posting")."""

    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "owner"})

    with pytest.raises(HTTPException) as exc_info:
        await thread_runs.proxy_dashboard_thread_run_cancel("tid", "run-1", "intruder")
    assert exc_info.value.status_code == 404


async def test_read_endpoints_accessible_by_non_owner(monkeypatch) -> None:
    """Read endpoints (state, stream, history) are accessible by any org member."""

    _install_client(monkeypatch, thread_metadata={"source": "slack", "github_login": "owner"})

    state = await thread_runs.get_dashboard_thread_state("tid", "teammate")
    assert "values" in state

    # stream/events preflight should not raise.
    await thread_runs.proxy_dashboard_thread_stream_events(
        "tid", "teammate", b"{}", content_type="application/json"
    )

    proxy = _install_proxy(monkeypatch)
    await thread_runs.proxy_dashboard_thread_history("tid", "teammate", b'{"limit": 20}')
    await thread_runs.proxy_dashboard_thread_history(
        "tid", "teammate", b'{"limit": 20, "metadata": {"run_id": "run-1"}}'
    )
    assert proxy.payloads == [
        {"limit": _DISCOVERY_HISTORY_LIMIT},
        {"limit": 20, "metadata": {"run_id": "run-1"}},
    ]
    with pytest.raises(HTTPException) as exc_info:
        await thread_runs.proxy_dashboard_thread_history("tid", "teammate", b"\xff")
    assert exc_info.value.status_code == 400


async def test_thread_state_uses_current_run_status_when_checkpoint_is_stale(monkeypatch) -> None:
    _install_client(
        monkeypatch,
        thread_metadata={
            "source": "dashboard",
            "github_login": "owner",
            "latest_run_status": "success",
        },
        state={"values": {"messages": []}, "next": []},
        runs=[{"run_id": "run-1", "status": "running"}],
    )

    state = await thread_runs.get_dashboard_thread_state("tid", "owner")

    assert "next" not in state


async def test_read_endpoints_reject_non_surfaced_source(monkeypatch) -> None:
    """Threads with an unknown source are not readable by anyone."""
    _install_client(
        monkeypatch, thread_metadata={"source": "unknown-source", "github_login": "owner"}
    )

    with pytest.raises(HTTPException) as exc_info:
        await thread_runs.get_dashboard_thread_state("tid", "owner")
    assert exc_info.value.status_code == 404


async def test_stream_events_carries_the_platform_api_key(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-key")
    _install_client(monkeypatch, thread_metadata={"source": "dashboard", "github_login": "octocat"})
    proxy = _install_proxy(monkeypatch, chunks=(b"event: hello\n\n",))

    stream = await thread_runs.proxy_dashboard_thread_stream_events(
        "tid", "octocat", b"{}", content_type="application/json"
    )
    assert [chunk async for chunk in stream] == [b"event: hello\n\n"]

    request = proxy.requests[-1]
    assert request.method == "POST"
    assert request.url.endswith("/threads/tid/stream/events")
    assert request.headers == {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "X-API-Key": "ls-key",
    }


def _ttft_event(
    method: str, data: dict[str, object], *, namespace: list[str], event_id: str
) -> bytes:
    payload = {
        "type": "event",
        "event_id": event_id,
        "method": method,
        "params": {"namespace": namespace, "timestamp": 2_250, "data": data},
    }
    return f"event: {method}\r\ndata: {json.dumps(payload)}\r\n\r\n".encode()


async def test_run_ttft_observer_records_first_assistant_text(monkeypatch) -> None:
    stream_bytes = _ttft_event(
        "messages",
        {"event": "message-start", "role": "ai"},
        namespace=["agent"],
        event_id="1-0",
    ) + _ttft_event(
        "messages",
        {"event": "content-block-delta", "delta": {"type": "text-delta", "text": "Hello"}},
        namespace=["agent"],
        event_id="2-0",
    )
    proxy = _install_proxy(monkeypatch, chunks=(stream_bytes[:35], stream_bytes[35:]))
    record = AsyncMock()
    monkeypatch.setattr(thread_proxy, "record_dashboard_thread_ttft", record)

    await thread_proxy.observe_run_ttft("thread-1", "run-1", 1_000)

    record.assert_awaited_once_with(
        AssistantTextObservation(run_id="run-1", event_timestamp_ms=2_250),
        thread_id="thread-1",
        started_at_ms=1_000,
    )
    request = proxy.requests[-1]
    assert request.method == "GET"
    assert request.url.endswith("/threads/thread-1/runs/run-1/stream")
    assert request.headers == {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Last-Event-ID": "-1",
    }
    assert request.params == {"stream_mode": "messages"}
