import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent import background_tasks
from agent.background_tasks import monitor_background_tasks
from agent.tools.background_execute import (
    TASK_ROOT,
    _control_script,
    _launch_command,
    background_execute,
)

# _launch_command refuses to run without setsid, which macOS does not ship; the
# sandbox these tasks run in is always Linux.
requires_setsid = pytest.mark.skipif(
    shutil.which("setsid") is None, reason="setsid is unavailable on this host"
)


def _run_control(action: str, task_id: str) -> dict:
    result = subprocess.run(
        ["python3", "-c", _control_script(action, task_id)],
        capture_output=True,
        check=True,
        text=True,
    )
    return json.loads(result.stdout)


@requires_setsid
def test_background_command_returns_while_running_then_caps_output() -> None:
    task_id = f"test-{uuid.uuid4().hex}"
    task_dir = Path(TASK_ROOT, task_id)
    command = "python3 -c \"print('x' * 1200000)\"; sleep .5; echo done"
    try:
        started = time.monotonic()
        launched = subprocess.run(
            ["/bin/sh", "-c", _launch_command(task_id, command, 10)],
            capture_output=True,
            check=True,
            text=True,
            timeout=3,
        )
        assert time.monotonic() - started < 2
        assert json.loads(launched.stdout)["status"] == "running"

        deadline = time.monotonic() + 5
        while (state := _run_control("status", task_id))["status"] == "running":
            assert time.monotonic() < deadline
            time.sleep(0.1)

        assert state["status"] == "completed"
        assert state["exit_code"] == 0
        assert "bytes omitted" in state["output"]
        assert state["output"].endswith("done\n")
        assert len(state["output"].encode()) < 65_600
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


@requires_setsid
def test_background_command_active_limit() -> None:
    task_ids = [f"test-{uuid.uuid4().hex}" for _ in range(4)]
    try:
        for task_id in task_ids:
            task_dir = Path(TASK_ROOT, task_id)
            task_dir.mkdir(parents=True)
            task_dir.joinpath("state.json").write_text('{"status": "running"}')
        result = subprocess.run(
            ["/bin/sh", "-c", _launch_command(f"test-{uuid.uuid4().hex}", "true", 10)],
            capture_output=True,
            text=True,
            timeout=3,
        )
        assert result.returncode == 72
        assert "active task limit reached" in result.stderr
    finally:
        for task_id in task_ids:
            shutil.rmtree(Path(TASK_ROOT, task_id), ignore_errors=True)


@requires_setsid
def test_background_command_timeout_and_stop() -> None:
    for timeout, stop, expected in ((1, False, "timed_out"), (10, True, "stopped")):
        task_id = f"test-{uuid.uuid4().hex}"
        task_dir = Path(TASK_ROOT, task_id)
        try:
            subprocess.run(
                ["/bin/sh", "-c", _launch_command(task_id, "sleep 30", timeout)],
                capture_output=True,
                check=True,
                text=True,
                timeout=3,
            )
            if stop:
                assert _run_control("stop", task_id)["status"] == "stopped"
            deadline = time.monotonic() + 4
            while (state := _run_control("status", task_id))["status"] == "running":
                assert time.monotonic() < deadline
                time.sleep(0.1)
            assert state["status"] == expected
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)


async def test_background_task_cron_search_uses_metadata_not_graph_name() -> None:
    client = AsyncMock()
    client.crons.search.return_value = []
    client.crons.create.return_value = {"cron_id": "cron-1"}

    with patch("agent.background_tasks._client", return_value=client):
        cron_id = await background_tasks.ensure_background_task_cron("thread-1")

    assert cron_id == "cron-1"
    client.crons.search.assert_awaited_once_with(
        metadata={"kind": "background_tasks", "thread_id": "thread-1"}, limit=10
    )
    assert client.crons.create.await_args.args == ("scheduler",)


async def test_background_execute_reports_monitor_scheduling_failure() -> None:
    backend = AsyncMock()
    backend.aexecute.return_value = SimpleNamespace(exit_code=0)

    with (
        patch(
            "agent.tools.background_execute._current_backend", return_value=("thread-1", backend)
        ),
        patch(
            "agent.tools.background_execute._execute",
            AsyncMock(
                side_effect=[
                    {"tasks": []},
                    {"task_id": "task-1", "status": "running"},
                ]
            ),
        ),
        patch(
            "agent.background_tasks.ensure_background_task_cron",
            AsyncMock(side_effect=RuntimeError("invalid assistant ID")),
        ),
    ):
        result = await background_execute("sleep 10")

    assert result == {
        "success": False,
        "task_id": "task-1",
        "status": "running",
        "error": "command started, but automatic completion monitoring could not be scheduled",
    }


async def test_monitor_coalesces_new_completions_and_reconciles_delivered_tasks() -> None:
    tasks = [
        {
            "task_id": f"task-{index}",
            "status": "completed",
            "exit_code": 0,
            "duration_seconds": 1,
            "output_path": f"/tmp/output-{index}.log",
            "notification": "pending",
        }
        for index in range(1, 4)
    ]
    backend = AsyncMock()
    backend.aexecute.return_value = SimpleNamespace(exit_code=0)
    client = AsyncMock()
    client.threads.get.return_value = {"metadata": {"sandbox_id": "sandbox-1"}}
    client.runs.list.return_value = [{"metadata": {"background_task_ids": ["task-1"]}}]

    with (
        patch("agent.background_tasks._client", return_value=client),
        patch("agent.background_tasks.create_sandbox", AsyncMock(return_value=backend)),
        patch(
            "agent.background_tasks._list_tasks",
            AsyncMock(side_effect=[tasks, [{**task, "notification": "done"} for task in tasks]]),
        ),
        patch("agent.background_tasks._claim", AsyncMock(return_value=True)),
        patch("agent.background_tasks._mark_delivered", AsyncMock()) as mark_delivered,
        patch("agent.background_tasks.dispatch_agent_run", AsyncMock()) as dispatch,
        patch("agent.background_tasks._delete_crons", AsyncMock()) as delete_crons,
    ):
        result = await monitor_background_tasks("thread-1")

    assert result == {"status": "idle", "delivered": 3}
    dispatch.assert_awaited_once()
    assert dispatch.await_args is not None
    assert "Task: task-1" not in dispatch.await_args.args[1]
    assert "Task: task-2" in dispatch.await_args.args[1]
    assert "Task: task-3" in dispatch.await_args.args[1]
    assert dispatch.await_args.kwargs["metadata"]["background_task_ids"] == ["task-2", "task-3"]
    assert dispatch.await_args.kwargs["multitask_strategy"] == "enqueue"
    assert mark_delivered.await_count == 3
    delete_crons.assert_awaited_once_with("thread-1")
