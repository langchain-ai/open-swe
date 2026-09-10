from typing import Any

from agent.dashboard.threads import api as thread_api
from agent.dashboard.threads import listing as thread_listing
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
        self.status = status
        self.run_id = run_id

    async def list(self, thread_id: str, limit: int = 1) -> list[dict[str, str]]:
        assert thread_id == "tid"
        assert limit == 1
        return [{"run_id": self.run_id, "status": self.status}]


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


async def test_list_dashboard_threads_refreshes_finished_run_status(monkeypatch) -> None:
    client = FakeClient(
        {
            "source": "dashboard",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": "pending",
        },
        "success",
    )
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    results = await thread_listing.list_dashboard_threads("octocat")

    assert results[0]["status"] == "finished"
    assert results[0]["viewed"] is False
    assert client.threads.thread["metadata"]["latest_run_status"] == "success"


async def test_get_dashboard_thread_marks_finished_thread_viewed(monkeypatch) -> None:
    client = FakeClient(
        {
            "source": "dashboard",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": "success",
        },
        "success",
    )
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    result = await thread_api.get_dashboard_thread("tid", "octocat")

    assert result["status"] == "finished"
    assert result["viewed"] is True
    assert isinstance(result["viewedAt"], int)
    assert client.threads.thread["metadata"]["last_viewed_run_id"] == "run-1"


async def test_get_dashboard_thread_marks_viewed_for_any_authenticated_user(monkeypatch) -> None:
    client = FakeClient(
        {
            "source": "slack",
            "latest_run_id": "run-1",
            "latest_run_status": "success",
        },
        "success",
    )
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    result = await thread_api.get_dashboard_thread("tid", "someone-else")

    assert result["status"] == "finished"
    assert client.threads.thread["metadata"]["last_viewed_run_id"] == "run-1"


async def test_get_dashboard_thread_skips_mark_viewed_when_disabled(monkeypatch) -> None:
    client = FakeClient(
        {
            "source": "dashboard",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": "success",
        },
        "success",
    )
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    result = await thread_api.get_dashboard_thread("tid", "octocat", mark_viewed=False)

    assert result["status"] == "finished"
    assert result["viewed"] is False
    assert "last_viewed_run_id" not in client.threads.thread["metadata"]


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
    assert result["queuedMessages"] == []
    assert "last_viewed_run_id" not in client.threads.thread["metadata"]


async def test_get_dashboard_thread_hydrates_queued_dashboard_messages(monkeypatch) -> None:
    client = FakeClient(
        {
            "source": "dashboard",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": "running",
        },
        "running",
        thread_status="busy",
        queued={
            "value": {
                "messages": [
                    {
                        "content": {
                            "source": "dashboard",
                            "text": "queued follow-up",
                            "queue_id": "queued-server-id",
                            "created_at_ms": 1700000000123,
                            "images": [
                                {
                                    "type": "image",
                                    "base64": "image-data",
                                    "mime_type": "image/png",
                                    "file_name": "proof.png",
                                }
                            ],
                        }
                    },
                    {"content": {"source": "slack", "text": "hidden"}},
                    {"content": {"source": "dashboard", "text": "incomplete"}},
                ]
            }
        },
    )
    patch_thread_module(monkeypatch, "langgraph_client", lambda: client)

    result = await thread_api.get_dashboard_thread("tid", "octocat")

    assert result["queuedMessages"] == [
        {
            "id": "queued-server-id",
            "content": "queued follow-up",
            "createdAt": 1700000000123,
            "images": [
                {
                    "kind": "image",
                    "base64": "image-data",
                    "mimeType": "image/png",
                    "fileName": "proof.png",
                }
            ],
        }
    ]
