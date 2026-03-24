"""Tests for the github_ci tools."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.tools.github_ci as github_ci


def _make_response(status_code: int, json_data: Any) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = str(json_data)
    return resp


# ---------------------------------------------------------------------------
# get_pr_check_runs
# ---------------------------------------------------------------------------


def test_get_pr_check_runs_no_repo_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_ci, "_get_repo_config", lambda: {})
    result = github_ci.get_pr_check_runs(42)
    assert result == {"success": False, "error": "No repo config found"}


def test_get_pr_check_runs_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_ci, "_get_repo_config", lambda: {"owner": "langchain-ai", "name": "open-swe"}
    )
    monkeypatch.setattr(github_ci, "_get_token", AsyncMock(return_value=None))
    result = github_ci.get_pr_check_runs(42)
    assert result == {"success": False, "error": "Failed to get GitHub App installation token"}


def test_get_pr_check_runs_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_ci, "_get_repo_config", lambda: {"owner": "langchain-ai", "name": "open-swe"}
    )
    monkeypatch.setattr(github_ci, "_get_token", AsyncMock(return_value="test-token"))

    pr_data = {"head": {"sha": "abc123"}}
    check_runs_data = {
        "total_count": 2,
        "check_runs": [
            {
                "id": 1,
                "name": "test-job",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/checks/1",
            },
            {
                "id": 2,
                "name": "lint-job",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://github.com/checks/2",
            },
        ],
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _make_response(200, pr_data),
            _make_response(200, check_runs_data),
        ]
    )

    mock_async_context = MagicMock()
    mock_async_context.__aenter__ = AsyncMock(return_value=mock_client)
    mock_async_context.__aexit__ = AsyncMock(return_value=False)

    with patch("agent.tools.github_ci.httpx.AsyncClient", return_value=mock_async_context):
        result = github_ci.get_pr_check_runs(42)

    assert result["success"] is True
    assert result["head_sha"] == "abc123"
    assert result["total_count"] == 2
    assert len(result["check_runs"]) == 2
    assert result["all_passed"] is True
    assert result["any_failed"] is False
    assert result["any_pending"] is False


def test_get_pr_check_runs_with_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_ci, "_get_repo_config", lambda: {"owner": "langchain-ai", "name": "open-swe"}
    )
    monkeypatch.setattr(github_ci, "_get_token", AsyncMock(return_value="test-token"))

    pr_data = {"head": {"sha": "def456"}}
    check_runs_data = {
        "total_count": 2,
        "check_runs": [
            {
                "id": 10,
                "name": "test-job",
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://github.com/checks/10",
            },
            {
                "id": 11,
                "name": "lint-job",
                "status": "in_progress",
                "conclusion": None,
                "html_url": "https://github.com/checks/11",
            },
        ],
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _make_response(200, pr_data),
            _make_response(200, check_runs_data),
        ]
    )

    mock_async_context = MagicMock()
    mock_async_context.__aenter__ = AsyncMock(return_value=mock_client)
    mock_async_context.__aexit__ = AsyncMock(return_value=False)

    with patch("agent.tools.github_ci.httpx.AsyncClient", return_value=mock_async_context):
        result = github_ci.get_pr_check_runs(42)

    assert result["success"] is True
    assert result["any_failed"] is True
    assert result["any_pending"] is True
    assert result["all_passed"] is False


def test_get_pr_check_runs_pr_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_ci, "_get_repo_config", lambda: {"owner": "langchain-ai", "name": "open-swe"}
    )
    monkeypatch.setattr(github_ci, "_get_token", AsyncMock(return_value="test-token"))

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=_make_response(404, {"message": "Not Found"}))

    mock_async_context = MagicMock()
    mock_async_context.__aenter__ = AsyncMock(return_value=mock_client)
    mock_async_context.__aexit__ = AsyncMock(return_value=False)

    with patch("agent.tools.github_ci.httpx.AsyncClient", return_value=mock_async_context):
        result = github_ci.get_pr_check_runs(42)

    assert result["success"] is False
    assert "404" in result["error"]


# ---------------------------------------------------------------------------
# rerun_failed_check_runs
# ---------------------------------------------------------------------------


def test_rerun_failed_check_runs_no_repo_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_ci, "_get_repo_config", lambda: {})
    result = github_ci.rerun_failed_check_runs(42)
    assert result == {"success": False, "error": "No repo config found"}


def test_rerun_failed_check_runs_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_ci, "_get_repo_config", lambda: {"owner": "langchain-ai", "name": "open-swe"}
    )
    monkeypatch.setattr(github_ci, "_get_token", AsyncMock(return_value=None))
    result = github_ci.rerun_failed_check_runs(42)
    assert result == {"success": False, "error": "Failed to get GitHub App installation token"}


def test_rerun_failed_check_runs_no_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_ci, "_get_repo_config", lambda: {"owner": "langchain-ai", "name": "open-swe"}
    )
    monkeypatch.setattr(github_ci, "_get_token", AsyncMock(return_value="test-token"))

    pr_data = {"head": {"sha": "abc999"}}
    workflow_runs_data = {
        "workflow_runs": [
            {"id": 100, "conclusion": "success"},
        ]
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _make_response(200, pr_data),
            _make_response(200, workflow_runs_data),
        ]
    )

    mock_async_context = MagicMock()
    mock_async_context.__aenter__ = AsyncMock(return_value=mock_client)
    mock_async_context.__aexit__ = AsyncMock(return_value=False)

    with patch("agent.tools.github_ci.httpx.AsyncClient", return_value=mock_async_context):
        result = github_ci.rerun_failed_check_runs(42)

    assert result["success"] is True
    assert result["rerun_run_ids"] == []
    assert "No failed workflow runs" in result["message"]


def test_rerun_failed_check_runs_with_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        github_ci, "_get_repo_config", lambda: {"owner": "langchain-ai", "name": "open-swe"}
    )
    monkeypatch.setattr(github_ci, "_get_token", AsyncMock(return_value="test-token"))

    pr_data = {"head": {"sha": "bbb111"}}
    workflow_runs_data = {
        "workflow_runs": [
            {"id": 200, "conclusion": "failure"},
            {"id": 201, "conclusion": "timed_out"},
        ]
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(
        side_effect=[
            _make_response(200, pr_data),
            _make_response(200, workflow_runs_data),
        ]
    )
    mock_client.post = AsyncMock(return_value=_make_response(201, {}))

    mock_async_context = MagicMock()
    mock_async_context.__aenter__ = AsyncMock(return_value=mock_client)
    mock_async_context.__aexit__ = AsyncMock(return_value=False)

    with patch("agent.tools.github_ci.httpx.AsyncClient", return_value=mock_async_context):
        result = github_ci.rerun_failed_check_runs(42)

    assert result["success"] is True
    assert set(result["rerun_run_ids"]) == {200, 201}
    assert len(result["rerun_results"]) == 2
    assert all(r["success"] for r in result["rerun_results"])
