"""Tests for project-scoped LangSmith trace tools."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.tools.langsmith_traces import (
    langsmith_get_run,
    langsmith_list_runs,
    langsmith_project_stats,
)

PROJECT_ID = "6a5cf28f-7c41-4ee9-a11e-696c74ddb5f6"
OTHER_PROJECT_ID = "00000000-0000-0000-0000-000000000000"


def _fake_run(
    *,
    run_id: str | None = None,
    session_id: str = PROJECT_ID,
    total_tokens: int = 1000,
    prompt_tokens: int = 800,
    completion_tokens: int = 200,
    total_cost: float = 0.01,
    latency_s: float = 2.5,
    error: str | None = None,
) -> SimpleNamespace:
    start = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    end = start + timedelta(seconds=latency_s)
    return SimpleNamespace(
        id=uuid.UUID(run_id) if run_id else uuid.uuid4(),
        name="agent",
        run_type="chain",
        status="success" if not error else "error",
        start_time=start,
        end_time=end,
        error=error,
        session_id=uuid.UUID(session_id),
        trace_id=uuid.uuid4(),
        parent_run_id=None,
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_cost=total_cost,
        tags=[],
        inputs={"x": 1},
        outputs={"y": 2},
    )


@pytest.fixture
def scoped_env():
    with patch.dict(
        "os.environ",
        {
            "LANGSMITH_TRACING_PROJECT_ID_PROD": PROJECT_ID,
            "LANGSMITH_API_KEY_PROD": "ls-fake-key",
        },
        clear=False,
    ):
        yield


class TestListRuns:
    def test_always_pins_project_id(self, scoped_env: None) -> None:
        """The tool must pass the env-var project_id on every call."""
        mock_client = MagicMock()
        mock_client.list_runs.return_value = iter([_fake_run()])
        with patch(
            "agent.tools.langsmith_traces.get_scoped_langsmith_client",
            return_value=mock_client,
        ):
            result = langsmith_list_runs(limit=10)
        assert "error" not in result
        assert result["project_id"] == PROJECT_ID
        assert result["count"] == 1
        _, kwargs = mock_client.list_runs.call_args
        assert kwargs["project_id"] == PROJECT_ID
        assert kwargs["is_root"] is True
        assert kwargs["limit"] == 10

    def test_caps_limit(self, scoped_env: None) -> None:
        mock_client = MagicMock()
        mock_client.list_runs.return_value = iter([])
        with patch(
            "agent.tools.langsmith_traces.get_scoped_langsmith_client",
            return_value=mock_client,
        ):
            langsmith_list_runs(limit=9999)
        _, kwargs = mock_client.list_runs.call_args
        assert kwargs["limit"] == 100

    def test_combines_end_time_and_filter(self, scoped_env: None) -> None:
        mock_client = MagicMock()
        mock_client.list_runs.return_value = iter([])
        with patch(
            "agent.tools.langsmith_traces.get_scoped_langsmith_client",
            return_value=mock_client,
        ):
            langsmith_list_runs(
                end_time_iso="2025-02-01T00:00:00Z",
                filter='eq(status, "error")',
            )
        _, kwargs = mock_client.list_runs.call_args
        assert kwargs["filter"].startswith("and(")
        assert 'eq(status, "error")' in kwargs["filter"]
        assert "2025-02-01T00:00:00Z" in kwargs["filter"]

    def test_rejects_invalid_iso(self, scoped_env: None) -> None:
        result = langsmith_list_runs(start_time_iso="not-a-date")
        assert "error" in result
        assert "start_time_iso" in result["error"]

    def test_missing_project_id(self) -> None:
        with patch.dict(
            "os.environ",
            {"LANGSMITH_TRACING_PROJECT_ID_PROD": "", "LANGSMITH_API_KEY_PROD": "k"},
        ):
            result = langsmith_list_runs()
        assert "error" in result
        assert "LANGSMITH_TRACING_PROJECT_ID_PROD" in result["error"]

    def test_missing_api_key(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "LANGSMITH_TRACING_PROJECT_ID_PROD": PROJECT_ID,
                "LANGSMITH_API_KEY_PROD": "",
                "LANGSMITH_API_KEY": "",
            },
        ):
            result = langsmith_list_runs()
        assert "error" in result
        assert "LANGSMITH_API_KEY" in result["error"]


class TestGetRun:
    def test_returns_run_in_scope(self, scoped_env: None) -> None:
        run_id = str(uuid.uuid4())
        mock_client = MagicMock()
        mock_client.read_run.return_value = _fake_run(run_id=run_id, session_id=PROJECT_ID)
        with patch(
            "agent.tools.langsmith_traces.get_scoped_langsmith_client",
            return_value=mock_client,
        ):
            result = langsmith_get_run(run_id)
        assert "error" not in result
        assert result["run"]["id"] == run_id
        assert result["run"]["inputs"] == {"x": 1}

    def test_rejects_run_from_other_project(self, scoped_env: None) -> None:
        """Defense in depth: a run from a different project must be refused."""
        run_id = str(uuid.uuid4())
        mock_client = MagicMock()
        mock_client.read_run.return_value = _fake_run(run_id=run_id, session_id=OTHER_PROJECT_ID)
        with patch(
            "agent.tools.langsmith_traces.get_scoped_langsmith_client",
            return_value=mock_client,
        ):
            result = langsmith_get_run(run_id)
        assert "error" in result
        assert "scoped tracing project" in result["error"]
        assert "run" not in result


class TestProjectStats:
    def test_aggregates_tokens_cost_and_errors(self, scoped_env: None) -> None:
        runs = [
            _fake_run(total_tokens=1000, total_cost=0.01, latency_s=1.0),
            _fake_run(total_tokens=2000, total_cost=0.02, latency_s=2.0),
            _fake_run(total_tokens=500, total_cost=0.005, latency_s=10.0, error="boom"),
        ]
        mock_client = MagicMock()
        mock_client.list_runs.return_value = iter(runs)
        with patch(
            "agent.tools.langsmith_traces.get_scoped_langsmith_client",
            return_value=mock_client,
        ):
            result = langsmith_project_stats()
        assert result["project_id"] == PROJECT_ID
        assert result["run_count"] == 3
        assert result["error_count"] == 1
        assert result["tokens"]["total"] == 3500
        assert result["cost_usd"]["total"] == pytest.approx(0.035, rel=1e-3)
        assert result["latency_seconds"]["max"] == 10.0
        assert result["latency_seconds"]["p50"] == 2.0

    def test_pins_project_id(self, scoped_env: None) -> None:
        mock_client = MagicMock()
        mock_client.list_runs.return_value = iter([])
        with patch(
            "agent.tools.langsmith_traces.get_scoped_langsmith_client",
            return_value=mock_client,
        ):
            langsmith_project_stats()
        _, kwargs = mock_client.list_runs.call_args
        assert kwargs["project_id"] == PROJECT_ID
        assert kwargs["is_root"] is True

    def test_empty_project(self, scoped_env: None) -> None:
        mock_client = MagicMock()
        mock_client.list_runs.return_value = iter([])
        with patch(
            "agent.tools.langsmith_traces.get_scoped_langsmith_client",
            return_value=mock_client,
        ):
            result = langsmith_project_stats()
        assert result["run_count"] == 0
        assert result["error_rate"] == 0.0
        assert result["latency_seconds"]["p50"] is None
