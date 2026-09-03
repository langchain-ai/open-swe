from unittest.mock import AsyncMock, patch

import pytest

from agent.review.styles import REVIEW_STYLES, ReviewStyle, reconcile_running_status


@pytest.mark.asyncio
async def test_reconcile_running_marks_completed_when_prompt_saved() -> None:
    record = ReviewStyle(
        full_name="acme/repo",
        status="running",
        custom_prompt="Prefer concrete runtime checks.",
    )
    with patch.object(
        REVIEW_STYLES,
        "mark_completed",
        new_callable=AsyncMock,
        return_value=record.model_copy(update={"status": "completed"}),
    ) as mock_up:
        out = await reconcile_running_status(
            "acme/repo", record, run_status="success", run_missing=False
        )
    mock_up.assert_awaited_once()
    assert out.status == "completed"


@pytest.mark.asyncio
async def test_reconcile_running_marks_failed_when_run_success_without_prompt() -> None:
    record = ReviewStyle(full_name="acme/repo", status="running", custom_prompt=None)
    with patch.object(
        REVIEW_STYLES,
        "mark_failed",
        new_callable=AsyncMock,
        return_value=record.model_copy(update={"status": "failed"}),
    ) as mock_fail:
        out = await reconcile_running_status(
            "acme/repo", record, run_status="completed", run_missing=False
        )
    mock_fail.assert_awaited_once()
    assert out.status == "failed"


@pytest.mark.asyncio
async def test_reconcile_running_marks_completed_when_run_missing_but_prompt_exists() -> None:
    record = ReviewStyle(
        full_name="keycloak/keycloak",
        status="running",
        custom_prompt="Prioritize security boundaries.",
    )
    with patch.object(
        REVIEW_STYLES,
        "mark_completed",
        new_callable=AsyncMock,
        return_value=record.model_copy(update={"status": "completed"}),
    ) as mock_up:
        out = await reconcile_running_status(
            "keycloak/keycloak", record, run_status=None, run_missing=True
        )
    mock_up.assert_awaited_once()
    assert out.status == "completed"


@pytest.mark.asyncio
async def test_sync_preserves_running_when_langgraph_errors() -> None:
    from agent.review.style_jobs import sync_review_style_run_status

    record = ReviewStyle(
        full_name="acme/repo",
        status="running",
        analysis_thread_id="thread-1",
        analysis_run_id="run-1",
    )
    mock_client = AsyncMock()
    mock_client.runs.get = AsyncMock(side_effect=RuntimeError("network blip"))
    with (
        patch.object(
            REVIEW_STYLES,
            "get_or_seed",
            new_callable=AsyncMock,
            return_value=record,
        ),
        patch("agent.review.style_jobs._client", return_value=mock_client),
        patch(
            "agent.review.style_jobs.reconcile_running_status",
            new_callable=AsyncMock,
        ) as mock_reconcile,
    ):
        out = await sync_review_style_run_status("acme/repo")
    assert out == record
    mock_reconcile.assert_not_called()
