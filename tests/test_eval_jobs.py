from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.dashboard import eval_jobs


@pytest.fixture(autouse=True)
def _clear_procs():
    eval_jobs._PROCS.clear()
    yield
    eval_jobs._PROCS.clear()


@pytest.mark.asyncio
async def test_get_status_returns_idle_when_no_record() -> None:
    with patch.object(eval_jobs, "_get_record", new=AsyncMock(return_value=None)):
        status = await eval_jobs.get_reviewer_eval_status()
    assert status["status"] == "idle"
    assert status["name"] == eval_jobs.REVIEWER_EVAL_KEY


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
    """A poll on a worker without the local handle must not kill a live run."""
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
async def test_start_reviewer_eval_launches_subprocess() -> None:
    proc = MagicMock()
    proc.pid = 4321
    proc.returncode = None
    create = AsyncMock(return_value=proc)

    def _consume(coro):
        coro.close()
        return MagicMock()

    with (
        patch.object(eval_jobs.asyncio, "create_subprocess_exec", new=create),
        patch.object(eval_jobs.asyncio, "create_task", new=_consume),
        patch.object(eval_jobs, "_get_record", new=AsyncMock(return_value=None)),
        patch.object(eval_jobs, "_put_record", new=AsyncMock(side_effect=lambda r: r)),
        patch.object(eval_jobs, "_resolve_langgraph_url", return_value="https://lg.test"),
    ):
        record = await eval_jobs.start_reviewer_eval(limit=3, created_by="octo")

    assert record["status"] == "running"
    assert record["limit"] == 3
    assert record["pid"] == 4321
    assert record["heartbeat"] is not None
    assert record["worker_id"] == eval_jobs._WORKER_ID
    assert eval_jobs._PROCS[eval_jobs.REVIEWER_EVAL_KEY] is proc

    args, kwargs = create.call_args
    assert "--limit" in args and "3" in args
    assert kwargs["env"]["LANGSMITH_PROJECT"] == eval_jobs.DEFAULT_EVAL_PROJECT
    assert kwargs["env"]["LANGGRAPH_URL"] == "https://lg.test"


@pytest.mark.asyncio
async def test_start_reviewer_eval_rejects_when_running() -> None:
    running = MagicMock()
    running.returncode = None
    eval_jobs._PROCS[eval_jobs.REVIEWER_EVAL_KEY] = running
    with pytest.raises(RuntimeError):
        await eval_jobs.start_reviewer_eval(limit=None, created_by="octo")


@pytest.mark.asyncio
async def test_stream_output_keeps_rolling_tail_and_experiment_url() -> None:
    url = "https://smith.langchain.com/o/x/experiments/abc"
    chunks = [
        f"starting eval {url}\n".encode(),
        *[f"row {i} done\n".encode() for i in range(2000)],
        b"",
    ]
    stdout = MagicMock()
    stdout.read = AsyncMock(side_effect=chunks)
    proc = MagicMock()
    proc.stdout = stdout

    tail, experiment_url = await eval_jobs._stream_output(proc)

    assert experiment_url == url
    assert len(tail) <= eval_jobs._LOG_TAIL_CHARS
    assert tail.endswith("row 1999 done\n")
    assert url not in tail  # scrolled out of the window but still captured
    assert eval_jobs.REVIEWER_EVAL_KEY not in tail


@pytest.mark.asyncio
async def test_start_reviewer_eval_rejects_fresh_run_on_other_worker() -> None:
    fresh = datetime.now(UTC).isoformat()
    record = {"name": "reviewer", "status": "running", "heartbeat": fresh}
    with patch.object(eval_jobs, "_get_record", new=AsyncMock(return_value=record)):
        with pytest.raises(RuntimeError):
            await eval_jobs.start_reviewer_eval(limit=None, created_by="octo")
