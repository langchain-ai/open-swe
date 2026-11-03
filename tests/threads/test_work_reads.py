import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

from openswe.dashboard.oauth import require_session
from openswe.threads import handlers, index, listing, routes, status, summary
from openswe.threads.index import ThreadStatus
from openswe.threads.runtimes import MdaRun
from openswe.threads.store import THREADS

pytestmark = pytest.mark.usefixtures("registry_db")


class WorkRuntime:
    def __init__(self) -> None:
        self.run: MdaRun | None = MdaRun(
            run_id="R1", status="success", created_at=datetime.now(UTC)
        )
        self.read: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    async def latest_run(self, thread_id: str) -> MdaRun | None:
        run = self.run
        if self.read is not None and self.release is not None:
            self.read.set()
            await self.release.wait()
            self.read = self.release = None
        return run

    async def status(self, thread_id: str) -> ThreadStatus:
        return "busy" if self.run is not None and self.run.active else "idle"


@pytest.fixture
async def work(monkeypatch: pytest.MonkeyPatch) -> WorkRuntime:
    runtime = WorkRuntime()
    monkeypatch.setattr(status.RUNTIMES, "runs_on", Mock(return_value=runtime))
    monkeypatch.setattr(THREADS.copies, "start", Mock())
    monkeypatch.setattr(summary, "get_langsmith_trace_url", AsyncMock(return_value=None))
    await index.create(
        "work",
        runtime="mda",
        metadata={
            "experience": "agent_workspace",
            "source": "dashboard",
            "visibility": "private",
            "owner_login": "test-user",
            "latest_run_id": "R1",
            "latest_run_status": "success",
            "last_viewed_run_id": "R1",
            "last_viewed_at_ms": 123,
        },
    )
    return runtime


async def test_fast_external_run_becomes_unread_without_an_intervening_list(
    work: WorkRuntime,
) -> None:
    work.run = MdaRun(run_id="R2", status="success", created_at=datetime.now(UTC))

    assert await status.refresh_thread_status("work") == "idle"
    thread = await THREADS.get("work")
    assert thread is not None
    result = await summary._thread_summary(thread.as_langgraph(), bridge_status=False)
    assert (result["latestRunId"], result["status"], result["viewed"]) == ("R2", "finished", False)
    assert listing._should_refresh_latest_run(thread.as_langgraph()) is False


async def test_settled_work_recovers_once_and_failed_recovery_is_retryable(
    work: WorkRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    work.run = MdaRun(run_id="R2", status="success", created_at=datetime.now(UTC))
    thread = await THREADS.get("work")
    assert thread is not None
    assert listing._should_refresh_latest_run(thread.as_langgraph())
    latest = AsyncMock(side_effect=RuntimeError("runtime unavailable"))
    monkeypatch.setattr(work, "latest_run", latest)
    await summary._refresh_latest_run_metadata(object(), thread.as_langgraph())
    failed = await THREADS.get("work")
    assert failed is not None
    assert listing._should_refresh_latest_run(failed.as_langgraph())

    latest.side_effect = None
    latest.return_value = work.run
    refreshed, _, _ = await summary._refresh_latest_run_metadata(object(), failed.as_langgraph())
    assert listing._should_refresh_latest_run(refreshed) is False
    assert refreshed["metadata"]["latest_run_id"] == "R2"


@pytest.mark.parametrize("acknowledge", [False, True])
async def test_old_refresh_cannot_overwrite_a_new_start(
    work: WorkRuntime, acknowledge: bool
) -> None:
    thread = await THREADS.get("work")
    assert thread is not None
    state = status.WorkRunState(thread)
    work.read, work.release = asyncio.Event(), asyncio.Event()
    refresh = asyncio.create_task(state.refresh(viewed_run_id="R1" if acknowledge else None))
    await work.read.wait()
    work.run = MdaRun(run_id="R2", status="running", created_at=datetime.now(UTC))
    await THREADS.update("work", {"latest_run_id": "R2", "latest_run_status": "running"})
    await index.set_status("work", "busy")
    work.release.set()
    updated = await refresh
    assert updated.status == "busy"
    assert updated.metadata["latest_run_id"] == "R2"
    assert updated.metadata["latest_run_status"] == "running"
    assert updated.metadata["last_viewed_at_ms"] == 123
    assert await state.receipt() == {"runId": "R2", "viewed": False, "viewedAt": 123}


async def test_explicit_receipt_matches_only_the_displayed_terminal_run(work: WorkRuntime) -> None:
    work.run = MdaRun(run_id="R2", status="running", created_at=datetime.now(UTC))
    body = routes.ThreadViewedBody(run_id="R2")
    session = {"sub": "test-user"}
    running = await routes.api_mark_thread_viewed("work", body, session)
    assert running == {"runId": "R2", "viewed": False, "viewedAt": 123}
    work.run.status = "success"
    mismatch = await routes.api_mark_thread_viewed(
        "work", routes.ThreadViewedBody(run_id="R1"), session
    )
    assert mismatch == running
    acknowledged = await routes.api_mark_thread_viewed("work", body, session)
    assert acknowledged["runId"] == "R2"
    assert acknowledged["viewed"] is True
    assert isinstance(acknowledged["viewedAt"], int) and acknowledged["viewedAt"] > 123
    thread = await THREADS.get("work")
    assert thread is not None
    assert await routes.api_mark_thread_viewed("work", body, session) == acknowledged
    unchanged = await THREADS.get("work")
    assert unchanged is not None and unchanged.updated_at == thread.updated_at

    work.run = MdaRun(run_id="R3", status="success", created_at=datetime.now(UTC))
    stale = await routes.api_mark_thread_viewed("work", body, session)
    assert stale["runId"] == "R3"
    assert stale["viewed"] is False
    assert stale["viewedAt"] == acknowledged["viewedAt"]


async def test_receipts_require_a_person_session_read_access_and_work(
    work: WorkRuntime,
) -> None:
    app = FastAPI()
    app.include_router(routes.router, prefix="/dashboard/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/dashboard/api/threads/work/viewed",
            json={"run_id": "R1"},
            headers={"Authorization": "Bearer machine-key"},
        )
        assert response.status_code == 401
        app.dependency_overrides[require_session] = lambda: {"sub": "someone-else"}
        response = await client.post("/dashboard/api/threads/work/viewed", json={"run_id": "R1"})
        assert response.status_code == 404
        app.dependency_overrides[require_session] = lambda: {"sub": "test-user"}
        response = await client.post("/dashboard/api/threads/work/viewed", json={"run_id": "R1"})
        assert response.status_code == 200
        assert response.json()["runId"] == "R1"
        assert response.json()["viewed"] is True
        assert isinstance(response.json()["viewedAt"], int)
        await THREADS.update("work", {"experience": "coding"})
        response = await client.post("/dashboard/api/threads/work/viewed", json={"run_id": "R1"})
        assert response.status_code == 409


@pytest.mark.parametrize(
    ("experience", "transcript", "expected_viewed"),
    [("agent_workspace", "v2", False), ("agent_workspace", None, True), ("coding", "v2", True)],
)
async def test_detail_keeps_explicit_work_receipts_and_legacy_read_fallback(
    work: WorkRuntime,
    monkeypatch: pytest.MonkeyPatch,
    experience: str,
    transcript: str | None,
    expected_viewed: bool,
) -> None:
    await THREADS.update(
        "work", {"experience": experience, "transcript": transcript, "last_viewed_run_id": "older"}
    )
    thread = await THREADS.get("work")
    assert thread is not None
    client = Mock()
    client.threads.get = AsyncMock(return_value=thread.as_langgraph())
    client.threads.update = AsyncMock(side_effect=THREADS.update)
    assert work.run is not None
    client.runs.list = AsyncMock(return_value=[work.run.model_dump(mode="json")])
    monkeypatch.setattr(handlers, "langgraph_client", Mock(return_value=client))
    monkeypatch.setattr(handlers, "attach_task_workers", AsyncMock())
    monkeypatch.setattr(handlers, "attach_subagents", AsyncMock())
    result = await handlers.get_dashboard_thread("work", "test-user")
    assert result["viewed"] is expected_viewed
    current = await THREADS.get("work")
    assert current is not None
    assert current.metadata["last_viewed_run_id"] == ("R1" if expected_viewed else "older")
    assert ("latestRunId" in result) is (experience == "agent_workspace")


@pytest.mark.parametrize("channel", [None, "linear"])
async def test_missing_runtime_settles_work_without_marking_it_read(
    work: WorkRuntime, channel: str | None
) -> None:
    work.run = None
    await THREADS.update("work", {"channel": channel, "latest_run_status": "running"})
    await index.set_status("work", "busy")
    assert await status.refresh_thread_status("work") == ("busy" if channel else "idle")
    assert await status.refresh_thread_status("work", unstarted_is_idle=True) == "idle"
    thread = await THREADS.get("work")
    assert thread is not None
    result = await summary._thread_summary(thread.as_langgraph(), bridge_status=False)
    assert result["status"] == "idle"
    assert thread.metadata["last_viewed_at_ms"] == 123


async def test_no_run_keeps_terminal_identity_without_acknowledging(work: WorkRuntime) -> None:
    work.run = None
    await THREADS.update("work", {"latest_run_status": "success"})
    result = await routes.api_mark_thread_viewed(
        "work", routes.ThreadViewedBody(run_id="R1"), {"sub": "test-user"}
    )
    assert result["viewedAt"] == 123
    thread = await THREADS.get("work")
    assert thread is not None
    result = await summary._thread_summary(thread.as_langgraph(), bridge_status=False)
    assert result["status"] == "finished"
    assert thread.metadata["latest_run_id"] == "R1"
