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
