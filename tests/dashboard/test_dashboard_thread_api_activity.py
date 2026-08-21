from typing import Any

from support.langgraph_fakes import FakeLangGraphClient

from agent.dashboard import thread_api


def _client(
    metadata: dict[str, Any], run_status: str, *, thread_status: str = "idle"
) -> FakeLangGraphClient:
    return FakeLangGraphClient(
        threads=[{"thread_id": "tid", "status": thread_status, "metadata": dict(metadata)}],
        runs={"tid": [{"run_id": "run-1", "status": run_status}]},
    )


def _metadata(client: FakeLangGraphClient) -> dict[str, Any]:
    return client.threads.threads["tid"]["metadata"]


async def test_list_dashboard_threads_refreshes_finished_run_status(monkeypatch) -> None:
    client = _client(
        {
            "source": "dashboard",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": "pending",
        },
        "success",
    )
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)

    results = await thread_api.list_dashboard_threads("octocat")

    assert results[0]["status"] == "finished"
    assert results[0]["viewed"] is False
    assert _metadata(client)["latest_run_status"] == "success"
    assert client.runs.list_calls == [{"thread_id": "tid", "limit": 1, "status": None}]


async def test_get_dashboard_thread_marks_finished_thread_viewed(monkeypatch) -> None:
    client = _client(
        {
            "source": "dashboard",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": "success",
        },
        "success",
    )
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)

    result = await thread_api.get_dashboard_thread("tid", "octocat")

    assert result["status"] == "finished"
    assert result["viewed"] is True
    assert isinstance(result["viewedAt"], int)
    assert _metadata(client)["last_viewed_run_id"] == "run-1"


async def test_get_dashboard_thread_readable_by_non_owner(monkeypatch) -> None:
    client = _client(
        {
            "source": "slack",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": "success",
        },
        "success",
    )
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)

    result = await thread_api.get_dashboard_thread("tid", "someone-else")

    assert result["status"] == "finished"
    assert "last_viewed_run_id" not in _metadata(client)


async def test_get_dashboard_thread_skips_mark_viewed_when_disabled(monkeypatch) -> None:
    client = _client(
        {
            "source": "dashboard",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": "success",
        },
        "success",
    )
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)

    result = await thread_api.get_dashboard_thread("tid", "octocat", mark_viewed=False)

    assert result["status"] == "finished"
    assert result["viewed"] is False
    assert "last_viewed_run_id" not in _metadata(client)


async def test_get_dashboard_thread_does_not_mark_running_thread_viewed(monkeypatch) -> None:
    client = _client(
        {
            "source": "dashboard",
            "github_login": "octocat",
            "latest_run_id": "run-1",
            "latest_run_status": "running",
        },
        "running",
        thread_status="busy",
    )
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)

    result = await thread_api.get_dashboard_thread("tid", "octocat")

    assert result["status"] == "running"
    assert result["viewed"] is False
    assert "last_viewed_run_id" not in _metadata(client)
