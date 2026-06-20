from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from evals.reviewer import store_reporter
from evals.reviewer.store_reporter import StoreReporter, github_run_url, is_enabled

_CONFIG = {
    "experiment_prefix": "openswe-review-confidence",
    "langsmith_project": "open-swe-evals",
    "model_id": "google_genai:gemini-3.5-flash",
}


def _make_reporter(
    monkeypatch: pytest.MonkeyPatch, completed: int = 0
) -> tuple[StoreReporter, MagicMock]:
    monkeypatch.setenv("LANGGRAPH_URL", "https://lg.test")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "langchain-ai/open-swe")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_ACTOR", "octocat")
    client = MagicMock()
    client.store.put_item = AsyncMock()
    with patch.object(store_reporter, "get_client", return_value=client):
        reporter = StoreReporter(
            config=dict(_CONFIG),
            limit=3,
            total=10,
            created_by=None,
            completed_getter=lambda: completed,
            tail_getter=lambda: "tail",
            experiment_url_getter=lambda: "https://smith.langchain.com/exp",
        )
    return reporter, client


def test_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REVIEWER_EVAL_REPORT_STORE", raising=False)
    monkeypatch.delenv("LANGGRAPH_URL", raising=False)
    assert is_enabled() is False
    monkeypatch.setenv("REVIEWER_EVAL_REPORT_STORE", "1")
    assert is_enabled() is False  # still needs LANGGRAPH_URL
    monkeypatch.setenv("LANGGRAPH_URL", "https://lg.test")
    assert is_enabled() is True


def test_github_run_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "langchain-ai/open-swe")
    monkeypatch.setenv("GITHUB_RUN_ID", "999")
    assert github_run_url() == "https://github.com/langchain-ai/open-swe/actions/runs/999"
    monkeypatch.delenv("GITHUB_RUN_ID")
    assert github_run_url() is None


@pytest.mark.asyncio
async def test_start_writes_running_record(monkeypatch: pytest.MonkeyPatch) -> None:
    reporter, client = _make_reporter(monkeypatch, completed=2)
    await reporter.start()

    client.store.put_item.assert_awaited_once()
    namespace, key, record = client.store.put_item.await_args.args
    assert namespace == ["evals"]
    assert key == "reviewer"
    assert record["status"] == "running"
    assert record["trigger"] == "github_action"
    assert record["progress"] == {"completed": 2, "total": 10}
    assert record["github_run_url"] == "https://github.com/langchain-ai/open-swe/actions/runs/12345"
    assert record["created_by"] == "octocat"  # falls back to GITHUB_ACTOR
    assert record["worker_id"] == "12345"
    assert record["run_name"] == "openswe-review-confidence"
    assert record["limit"] == 3
    assert record["heartbeat"]


@pytest.mark.asyncio
async def test_finish_writes_terminal_record(monkeypatch: pytest.MonkeyPatch) -> None:
    reporter, client = _make_reporter(monkeypatch)
    await reporter.finish(status="failed", error="boom")

    _, _, record = client.store.put_item.await_args.args
    assert record["status"] == "failed"
    assert record["error"] == "boom"
    assert record["finished_at"]


@pytest.mark.asyncio
async def test_put_swallows_store_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    reporter, client = _make_reporter(monkeypatch)
    client.store.put_item = AsyncMock(side_effect=RuntimeError("store down"))
    # Should not raise — store failures must not crash the eval.
    await reporter.start()
