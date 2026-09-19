"""The thread detail endpoint reports its phase breakdown in Server-Timing."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agent.threads import handlers as thread_api
from agent.threads import routes


async def test_get_thread_reports_server_timing_phases(monkeypatch) -> None:
    thread = {
        "thread_id": "thread-1",
        "status": "idle",
        "metadata": {"source": "dashboard", "created_by": "alice"},
    }
    client = SimpleNamespace(threads=SimpleNamespace(get=AsyncMock(return_value=thread)))
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    monkeypatch.setattr(thread_api, "assert_thread_readable", lambda *args: None)

    async def refresh(_client, current, *, timings=None):
        if timings is not None:
            timings["runs_list"] = 3.0
        return current, "success", "run-1"

    monkeypatch.setattr(thread_api, "_refresh_latest_run_metadata", refresh)
    monkeypatch.setattr(
        thread_api, "_mark_thread_viewed", AsyncMock(return_value=thread["metadata"])
    )
    monkeypatch.setattr(thread_api, "_thread_summary", AsyncMock(return_value={"id": "thread-1"}))

    response = await routes.api_get_thread("thread-1", True, {"sub": "alice"})

    assert json.loads(response.body) == {"id": "thread-1"}
    header = response.headers["Server-Timing"]
    phases = {part.split(";", 1)[0] for part in header.split(", ")}
    assert phases == {"thread_get", "runs_list", "mark_viewed", "summary", "total"}
    assert all(";dur=" in part for part in header.split(", "))
