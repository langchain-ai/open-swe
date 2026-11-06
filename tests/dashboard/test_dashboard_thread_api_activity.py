from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from openswe.dashboard.oauth import require_session
from openswe.threads import handlers as thread_api
from openswe.threads import listing, read_state, routes
from openswe.threads.principals import Principal, require_principal
from openswe.threads.runtimes import MdaRun
from tests.conftest import ThreadStoreOverClient, patch_thread_module


class FakeThreads:
    def __init__(self, metadata: dict[str, Any], *, status: str = "idle") -> None:
        self.thread = {"thread_id": "tid", "status": status, "metadata": metadata.copy()}
        self.updates: list[dict[str, Any]] = []

    async def get(self, thread_id: str) -> dict[str, Any]:
        assert thread_id == "tid"
        return self.thread

    async def get_state(self, thread_id: str) -> dict[str, Any]:
        assert thread_id == "tid"
        return {"values": {"messages": []}}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        assert thread_id == "tid"
        self.updates.append(metadata)
        self.thread["metadata"] = {**self.thread["metadata"], **metadata}

    async def search(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [self.thread]


class FakeRuns:
    def __init__(self, status: str, run_id: str = "run-1") -> None:
        # Newest first, as LangGraph lists them.
        self.runs = [{"run_id": run_id, "status": status}]

    async def list(
        self, thread_id: str, limit: int = 1, status: str | None = None
    ) -> list[dict[str, str]]:
        assert thread_id == "tid"
        assert limit == 1
        return [run for run in self.runs if status in (None, run["status"])][:limit]

    async def latest_run(self, thread_id: str) -> MdaRun | None:
        runs = await self.list(thread_id)
        return (
            MdaRun.model_validate({**runs[0], "created_at": "2026-11-01T00:00:00Z"})
            if runs
            else None
        )


class FakeClient:
    def __init__(
        self,
        metadata: dict[str, Any],
        run_status: str,
        *,
        thread_status: str = "idle",
    ) -> None:
        self.threads = FakeThreads(metadata, status=thread_status)
        self.runs = FakeRuns(run_status)


@pytest.mark.parametrize("run_status", ["running", "success", "error", "interrupted"])
async def test_get_dashboard_thread_does_not_mark_thread_viewed(monkeypatch, run_status) -> None:
    client = FakeClient(
        {
            "source": "dashboard",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": run_status,
        },
        run_status,
        thread_status="busy" if run_status == "running" else "idle",
    )
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    result = await thread_api.get_dashboard_thread("tid", "octocat")

    assert result["viewed"] is False
    assert "last_viewed_run_id" not in client.threads.thread["metadata"]
    assert client.threads.updates == []


async def test_follow_up_withdrawn_from_the_queue_leaves_the_thread_running(monkeypatch) -> None:
    # Cancelling the queued run also leaves LangGraph reporting the thread idle.
    client = FakeClient(
        {
            "source": "dashboard",
            "github_login": "octocat",
            "latest_run_id": "live",
            "latest_run_status": "running",
        },
        "running",
    )
    client.runs.runs = [
        {"run_id": "withdrawn", "status": "interrupted"},
        {"run_id": "live", "status": "running"},
    ]
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    result = await thread_api.get_dashboard_thread("tid", "octocat")

    assert result["status"] == "running"
    # What the commands proxy reads to steer or queue the next follow-up.
    metadata = client.threads.thread["metadata"]
    assert (metadata["latest_run_status"], metadata["latest_run_id"]) == ("running", "live")


class TestThreadReadState:
    @pytest.fixture(autouse=True)
    def context(self, monkeypatch, user_records) -> None:
        self.client = FakeClient(
            {
                "source": "dashboard",
                "owner_login": "octocat",
                "latest_run_id": "run-1",
                "latest_run_status": "success",
            },
            "success",
        )
        patch_thread_module(monkeypatch, "langgraph_client", lambda: self.client)
        monkeypatch.setattr(routes, "THREADS", ThreadStoreOverClient())
        monkeypatch.setattr(
            routes, "RUNTIMES", SimpleNamespace(runs_on=lambda runtime: self.client.runs)
        )
        monkeypatch.setattr(read_state, "postgres", SimpleNamespace(configured=lambda: True))
        self.session = {"sub": "octocat"}
        self.app = FastAPI()
        self.app.include_router(routes.router, prefix="/dashboard/api")
        self.app.dependency_overrides[require_session] = self.authenticated_session
        self.app.dependency_overrides[require_principal] = self.authenticated_principal

    async def authenticated_session(self) -> dict[str, str]:
        return self.session

    async def authenticated_principal(self) -> Principal:
        return Principal.of_login(self.session["sub"])

    @pytest.mark.parametrize("has_run", [True, False])
    async def test_read_unread_is_personal_and_survives_detail_polling(self, has_run: bool) -> None:
        if not has_run:
            self.client.threads.thread["metadata"].pop("latest_run_id")
            self.client.runs.runs = []
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            url = "/dashboard/api/threads/tid/read-state"
            response = await client.put(url, json={"viewed": True})
            assert response.status_code == 200
            assert response.json() == {"viewed": True}
            assert (await thread_api.get_dashboard_thread("tid", "octocat"))["viewed"] is True
            assert (await thread_api.get_dashboard_thread("tid", "other"))["viewed"] is False
            self.session = {"sub": "other"}
            assert (await client.put(url, json={"viewed": True})).status_code == 200
            self.session = {"sub": "octocat"}
            response = await client.put(url, json={"viewed": False})
            assert response.json() == {"viewed": False}
            response = await client.get("/dashboard/api/threads/tid?mark_viewed=true")
            assert response.status_code == 200
            assert response.json()["viewed"] is False
            response = await client.get("/dashboard/api/threads/tid")
            assert response.json()["viewed"] is False
            assert (await thread_api.get_dashboard_thread("tid", "other"))["viewed"] is True
            assert "last_viewed_run_id" not in self.client.threads.thread["metadata"]

    @pytest.mark.parametrize("action", ["mark_read", "mark_unread"])
    async def test_tool_changes_only_requesting_persons_read_state(
        self, monkeypatch, action
    ) -> None:
        from openswe.tools import threads as threads_tool

        await routes.api_set_thread_read_state(
            "tid", routes.ThreadReadState(viewed=True), {"sub": "other"}
        )
        monkeypatch.setattr(
            threads_tool,
            "_actor",
            AsyncMock(
                return_value=threads_tool._Actor(login="octocat", email=None, name="Octocat")
            ),
        )
        monkeypatch.setattr(threads_tool, "get_config", lambda: {"configurable": {}})
        monkeypatch.setattr(threads_tool, "get_dashboard_thread", thread_api.get_dashboard_thread)
        result = await threads_tool.manage_thread("tid", action)
        assert result == {"success": True, "thread_id": "tid", "viewed": action == "mark_read"}
        assert (await thread_api.get_dashboard_thread("tid", "octocat"))["viewed"] is (
            action == "mark_read"
        )
        assert (await thread_api.get_dashboard_thread("tid", "other"))["viewed"] is True

    async def test_new_run_is_unread_in_detail_and_lists(self) -> None:
        await routes.api_set_thread_read_state(
            "tid", routes.ThreadReadState(viewed=True), self.session
        )
        self.client.threads.thread["metadata"]["latest_run_id"] = "run-2"
        self.client.runs.runs = [{"run_id": "run-2", "status": "success"}]
        detail = await thread_api.get_dashboard_thread("tid", "octocat")
        assert detail["viewed"] is False
        assert detail["latestRunId"] == "run-2"
        summaries = await listing._summarize_threads(
            self.client, [self.client.threads.thread], viewer_login="octocat"
        )
        assert summaries[0]["viewed"] is False
        assert summaries[0]["latestRunId"] == "run-2"
        self.client.threads.thread["metadata"]["latest_run_id"] = "run-1"
        await routes.api_set_thread_read_state(
            "tid", routes.ThreadReadState(viewed=True), self.session
        )
        summaries = await listing._summarize_threads(
            self.client, [self.client.threads.thread], viewer_login="octocat"
        )
        assert summaries[0]["viewed"] is True
        summaries = await listing._summarize_threads(
            self.client, [self.client.threads.thread], viewer_login="other"
        )
        assert summaries[0]["viewed"] is False

    async def test_legacy_read_markers_belong_only_to_owner(self) -> None:
        self.client.threads.thread["metadata"].update(
            last_viewed_run_id="run-1", last_viewed_at_ms=123
        )
        assert (await thread_api.get_dashboard_thread("tid", "octocat"))["viewed"] is True
        assert (await thread_api.get_dashboard_thread("tid", "other"))["viewed"] is False
        await routes.api_set_thread_read_state(
            "tid", routes.ThreadReadState(viewed=False), self.session
        )
        assert (await thread_api.get_dashboard_thread("tid", "octocat"))["viewed"] is False

    async def test_review_walkthrough_uses_personal_view_time(self) -> None:
        metadata = self.client.threads.thread["metadata"]
        metadata.update(
            source="review_chat",
            github_login="octocat",
            repo_owner="acme",
            repo_name="repo",
            pr_number=1,
            walkthrough_state="ready",
            walkthrough_ready_at_ms=1,
        )
        await routes.api_set_thread_read_state(
            "tid", routes.ThreadReadState(viewed=True), self.session
        )
        result = await thread_api.get_dashboard_thread("tid", "octocat")
        assert result["viewed"] is True
        metadata["walkthrough_ready_at_ms"] = result["viewedAt"] + 1
        assert (await thread_api.get_dashboard_thread("tid", "octocat"))["viewed"] is False

    async def test_unreadable_and_missing_threads_cannot_change_read_state(
        self, monkeypatch, user_records
    ) -> None:
        self.client.threads.thread["metadata"]["visibility"] = "private"
        self.session = {"sub": "other"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/dashboard/api/threads/tid/read-state", json={"viewed": True}
            )
            assert response.status_code == 404
            monkeypatch.setattr(
                routes, "THREADS", SimpleNamespace(get=AsyncMock(return_value=None))
            )
            response = await client.put(
                "/dashboard/api/threads/missing/read-state", json={"viewed": False}
            )
            assert response.status_code == 404
            self.app.dependency_overrides.pop(require_session)
            response = await client.put(
                "/dashboard/api/threads/tid/read-state", json={"viewed": True}
            )
            assert response.status_code == 401
        assert user_records.items == {}
