from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard.review_styles import reconcile_running_status


@pytest.mark.asyncio
async def test_reconcile_running_marks_completed_when_prompt_saved() -> None:
    record = {
        "full_name": "acme/repo",
        "status": "running",
        "custom_prompt": "Prefer concrete runtime checks.",
    }
    with patch(
        "agent.dashboard.review_styles.update_review_style",
        new_callable=AsyncMock,
        return_value={**record, "status": "completed"},
    ) as mock_up:
        out = await reconcile_running_status(
            "acme/repo", record, run_status="success", run_missing=False
        )
    mock_up.assert_awaited_once()
    assert out["status"] == "completed"


@pytest.mark.asyncio
async def test_reconcile_running_marks_failed_when_run_success_without_prompt() -> None:
    record = {"full_name": "acme/repo", "status": "running", "custom_prompt": None}
    with patch(
        "agent.dashboard.review_styles.mark_analysis_failed",
        new_callable=AsyncMock,
        return_value={**record, "status": "failed"},
    ) as mock_fail:
        out = await reconcile_running_status(
            "acme/repo", record, run_status="completed", run_missing=False
        )
    mock_fail.assert_awaited_once()
    assert out["status"] == "failed"


@pytest.mark.asyncio
async def test_reconcile_running_marks_completed_when_run_missing_but_prompt_exists() -> None:
    record = {
        "full_name": "keycloak/keycloak",
        "status": "running",
        "custom_prompt": "Prioritize security boundaries.",
    }
    with patch(
        "agent.dashboard.review_styles.update_review_style",
        new_callable=AsyncMock,
        return_value={**record, "status": "completed"},
    ) as mock_up:
        out = await reconcile_running_status(
            "keycloak/keycloak", record, run_status=None, run_missing=True
        )
    mock_up.assert_awaited_once()
    assert out["status"] == "completed"


@pytest.mark.asyncio
async def test_sync_preserves_running_when_langgraph_errors() -> None:
    from agent.dashboard.review_style_jobs import sync_review_style_run_status

    record = {
        "full_name": "acme/repo",
        "status": "running",
        "analysis_thread_id": "thread-1",
        "analysis_run_id": "run-1",
    }
    mock_client = AsyncMock()
    mock_client.runs.get = AsyncMock(side_effect=RuntimeError("network blip"))
    with (
        patch(
            "agent.dashboard.review_style_jobs.get_review_style",
            new_callable=AsyncMock,
            return_value=record,
        ),
        patch("agent.dashboard.review_style_jobs._client", return_value=mock_client),
        patch(
            "agent.dashboard.review_style_jobs.reconcile_running_status",
            new_callable=AsyncMock,
        ) as mock_reconcile,
    ):
        out = await sync_review_style_run_status("acme/repo")
    assert out == record
    mock_reconcile.assert_not_called()
