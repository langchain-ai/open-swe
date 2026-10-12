from typing import Any

from openswe.threads import handlers as thread_api
from tests.conftest import patch_thread_module


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


class FakeStore:
    def __init__(self, queued: object = None) -> None:
        self.queued = queued

    async def get_item(self, namespace: tuple[str, str], key: str) -> object:
        assert namespace == ("queue", "tid")
        assert key == "pending_messages"
        return self.queued


class FakeClient:
    def __init__(
        self,
        metadata: dict[str, Any],
        run_status: str,
        *,
        thread_status: str = "idle",
        queued: object = None,
    ) -> None:
        self.threads = FakeThreads(metadata, status=thread_status)
        self.runs = FakeRuns(run_status)
        self.store = FakeStore(queued)


async def test_get_dashboard_thread_does_not_mark_running_thread_viewed(monkeypatch) -> None:
    client = FakeClient(
        {
            "source": "dashboard",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": "running",
        },
        "running",
        thread_status="busy",
    )
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    result = await thread_api.get_dashboard_thread("tid", "octocat")

    assert result["status"] == "running"
    assert result["viewed"] is False
    assert "last_viewed_run_id" not in client.threads.thread["metadata"]


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
