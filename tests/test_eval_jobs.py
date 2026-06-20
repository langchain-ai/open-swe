from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from agent.dashboard import eval_jobs


@pytest.mark.asyncio
async def test_get_status_returns_idle_when_no_record() -> None:
    with patch.object(eval_jobs, "_get_record", new=AsyncMock(return_value=None)):
        status = await eval_jobs.get_reviewer_eval_status()
    assert status["status"] == "idle"
    assert status["name"] == eval_jobs.REVIEWER_EVAL_KEY


@pytest.mark.asyncio
async def test_get_status_returns_terminal_record_unchanged() -> None:
    record = {"name": "reviewer", "status": "completed", "experiment_url": "https://x"}
    with (
        patch.object(eval_jobs, "_get_record", new=AsyncMock(return_value=record)),
        patch.object(eval_jobs, "_put_record", new=AsyncMock(side_effect=lambda r: r)) as put,
    ):
        status = await eval_jobs.get_reviewer_eval_status()
    assert status is record
    put.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_status_reconciles_stale_running() -> None:
    stale = (datetime.now(UTC) - timedelta(seconds=300)).isoformat()
    record = {"name": "reviewer", "status": "running", "heartbeat": stale}
    with (
        patch.object(eval_jobs, "_get_record", new=AsyncMock(return_value=record)),
        patch.object(eval_jobs, "_put_record", new=AsyncMock(side_effect=lambda r: r)) as put,
    ):
        status = await eval_jobs.get_reviewer_eval_status()
    assert status["status"] == "failed"
    assert "no longer tracked" in status["error"]
    put.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_status_keeps_running_with_fresh_heartbeat() -> None:
    """A poll must not kill a run whose Action is still heartbeating."""
    fresh = datetime.now(UTC).isoformat()
    record = {"name": "reviewer", "status": "running", "heartbeat": fresh}
    with (
        patch.object(eval_jobs, "_get_record", new=AsyncMock(return_value=record)),
        patch.object(eval_jobs, "_put_record", new=AsyncMock(side_effect=lambda r: r)) as put,
    ):
        status = await eval_jobs.get_reviewer_eval_status()
    assert status["status"] == "running"
    put.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_status_fails_running_without_heartbeat() -> None:
    """A running record with no heartbeat at all is treated as stale."""
    record = {"name": "reviewer", "status": "running"}
    with (
        patch.object(eval_jobs, "_get_record", new=AsyncMock(return_value=record)),
        patch.object(eval_jobs, "_put_record", new=AsyncMock(side_effect=lambda r: r)),
    ):
        status = await eval_jobs.get_reviewer_eval_status()
    assert status["status"] == "failed"
