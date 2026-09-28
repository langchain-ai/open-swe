import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from langgraph_sdk.errors import NotFoundError

from agent import background_tasks
from agent.background_tasks import monitor_background_tasks
from agent.slack import thinking as slack_thinking
from agent.tools.background_execute import (
    TASK_ROOT,
    _launch_command,
    background_execute,
    control_script,
)
from agent.utils.background_task_state import update_background_task_state

# _launch_command refuses to run without setsid, which macOS does not ship; the
# sandbox these tasks run in is always Linux.
requires_setsid = pytest.mark.skipif(
    shutil.which("setsid") is None, reason="setsid is unavailable on this host"
)


def _run_control(action: str, task_id: str) -> dict:
    result = subprocess.run(
        ["python3", "-c", control_script(action, task_id)],
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


async def test_background_execute_reports_monitor_scheduling_failure() -> None:
    backend = AsyncMock()
    backend.aexecute.return_value = SimpleNamespace(exit_code=0)

    with (
        patch("agent.tools.background_execute.update_background_task_state", AsyncMock()),
        patch(
            "agent.tools.background_execute._current_backend", return_value=("thread-1", backend)
        ),
        patch(
            "agent.tools.background_execute.execute",
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


@pytest.mark.parametrize("tracking_failure", [False, True])
async def test_monitor_enqueues_one_claimed_completion(tracking_failure: bool) -> None:
    task = {
        "task_id": "task-1",
        "status": "completed",
        "exit_code": 0,
        "duration_seconds": 1,
        "output_path": "/tmp/output.log",
        "notification": "pending",
    }
    backend = AsyncMock()
    backend.aexecute.return_value = SimpleNamespace(exit_code=0)
    client = AsyncMock()
    client.threads.get.return_value = {
        "metadata": {
            "sandbox_id": "sandbox-1",
            "source": "slack",
            "source_context": {"slack_thread": {"channel_id": "C123", "thread_ts": "123.45"}},
            "running_background_tasks": ["task-1"],
        }
    }

    with (
        patch("agent.background_tasks._client", return_value=client),
        patch(
            "agent.background_tasks.update_background_task_state",
            AsyncMock(
                side_effect=RuntimeError("metadata unavailable") if tracking_failure else None
            ),
        ),
        patch("agent.background_tasks.create_sandbox", AsyncMock(return_value=backend)),
        patch(
            "agent.background_tasks._list_tasks",
            AsyncMock(side_effect=[[task], [{**task, "notification": "done"}]]),
        ),
        patch("agent.background_tasks._claim", AsyncMock(return_value=True)),
        patch("agent.background_tasks._mark_delivered", AsyncMock()),
        patch("agent.background_tasks.dispatch_agent_run", AsyncMock()) as dispatch,
        patch("agent.background_tasks._delete_crons", AsyncMock()) as delete_crons,
    ):
        result = await monitor_background_tasks("thread-1")

    assert result == {"status": "idle", "delivered": 1}
    dispatch.assert_awaited_once()
    assert dispatch.await_args is not None
    configurable = dispatch.await_args.args[2]
    assert configurable["source"] == "slack"
    assert configurable["background_task_completion"] is True
    assert dispatch.await_args.kwargs["source"] == "slack"
    assert dispatch.await_args.kwargs["context"] == {
        "sender_id": "system:background-task",
        "surface": "automation",
        "kind": "system",
    }
    assert dispatch.await_args.kwargs["systems"] == [
        {
            "id": "system:background-task",
            "display_name": "Background task",
            "platform": "open-swe",
        }
    ]
    assert dispatch.await_args.kwargs["multitask_strategy"] == "enqueue"
    if tracking_failure:
        delete_crons.assert_not_awaited()
    else:
        delete_crons.assert_awaited_once_with("thread-1")


async def test_task_metadata_preserves_concurrent_launch() -> None:
    client = AsyncMock()
    client.threads.get.return_value = {
        "metadata": {"running_background_tasks": ["cmd-old", "cmd-concurrent"], "source": "slack"}
    }
    await update_background_task_state(client, "thread-1", finished=["cmd-old"])
    client.threads.update.assert_awaited_once_with(
        "thread-1", metadata={"running_background_tasks": ["cmd-concurrent"]}
    )


@pytest.mark.parametrize("tracking_failure", [False, True])
async def test_monitor_uses_fresh_state_for_concurrent_launch(
    monkeypatch: pytest.MonkeyPatch, tracking_failure: bool
) -> None:
    client = AsyncMock()
    metadata: dict[str, object] = {
        "sandbox_id": "sandbox-1",
        "running_background_tasks": ["cmd-old"],
        "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}},
    }
    current = {**metadata, "running_background_tasks": ["cmd-old", "cmd-concurrent"]}
    reads: list[object] = [{"metadata": metadata}, {"metadata": current}]
    if tracking_failure:
        reads.insert(1, RuntimeError("metadata unavailable"))
    client.threads.get.side_effect = reads
    client.runs.list.return_value = []
    backend = AsyncMock()
    backend.aexecute.return_value = SimpleNamespace(exit_code=0)
    monkeypatch.setattr(background_tasks, "_client", lambda: client)
    monkeypatch.setattr(background_tasks, "create_sandbox", AsyncMock(return_value=backend))
    monkeypatch.setattr(background_tasks, "_list_tasks", AsyncMock(return_value=[]))
    delete_crons = AsyncMock()
    monkeypatch.setattr(background_tasks, "_delete_crons", delete_crons)
    set_status = AsyncMock()
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)

    await monitor_background_tasks("thread-1")

    set_status.assert_awaited_once_with("C1", "1.0", "Waiting for background tasks…")
    assert client.threads.get.await_count == (3 if tracking_failure else 2)
    if tracking_failure:
        client.threads.update.assert_not_awaited()
        delete_crons.assert_not_awaited()
    else:
        client.threads.update.assert_awaited_once_with(
            "thread-1", metadata={"running_background_tasks": ["cmd-concurrent"]}
        )


@pytest.mark.parametrize("slack", [False, True])
@pytest.mark.parametrize("run_active", [False, True])
async def test_monitor_refreshes_task_state_after_completion_dispatch(
    monkeypatch: pytest.MonkeyPatch, slack: bool, run_active: bool
) -> None:
    from agent import dispatch, thread_feedback

    client = AsyncMock()
    stored: dict[str, object] = {
        "sandbox_id": "sandbox-1",
        "running_background_tasks": ["cmd-old"],
    }
    if slack:
        stored["source_context"] = {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}}

    async def get(thread_id: str) -> dict[str, object]:
        return {"metadata": dict(stored)}

    async def update(thread_id: str, *, metadata: dict[str, object]) -> None:
        stored.update(metadata)

    async def create(*args: object, **kwargs: object) -> dict[str, str]:
        stored["running_background_tasks"] = ["cmd-followup"]
        return {"run_id": "run-followup"}

    client.threads.get.side_effect = get
    client.threads.update.side_effect = update
    client.runs.create.side_effect = create
    client.runs.list.return_value = [{"run_id": "run-followup"}] if run_active else []
    backend = AsyncMock()
    backend.aexecute.return_value = SimpleNamespace(exit_code=0)
    monkeypatch.setattr(background_tasks, "_client", lambda: client)
    monkeypatch.setattr(dispatch, "dispatch_client", lambda: client)
    monkeypatch.setattr(thread_feedback, "note_feedback_activity", AsyncMock())
    monkeypatch.setattr(background_tasks, "create_sandbox", AsyncMock(return_value=backend))
    monkeypatch.setattr(
        background_tasks,
        "_list_tasks",
        AsyncMock(
            side_effect=[
                [{"task_id": "cmd-old", "status": "completed", "notification": "pending"}],
                [{"task_id": "cmd-followup", "status": "running"}],
            ]
        ),
    )
    monkeypatch.setattr(background_tasks, "_claim", AsyncMock(return_value=True))
    monkeypatch.setattr(background_tasks, "_mark_delivered", AsyncMock())
    set_status = AsyncMock()
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)

    result = await monitor_background_tasks("thread-1")

    assert result["delivered"] == 1
    assert stored["running_background_tasks"] == ["cmd-followup"]
    # Only an idle Slack settlement needs another snapshot after the run was created.
    assert client.threads.get.await_count == (4 if slack and not run_active else 2)
    client.threads.update.assert_awaited_once_with(
        "thread-1", metadata={"running_background_tasks": []}
    )
    if slack:
        assert set_status.await_args is not None
        assert set_status.await_args.args == (
            "C1",
            "1.0",
            "Thinking..." if run_active else "Waiting for background tasks…",
        )
    else:
        set_status.assert_not_awaited()
        client.runs.list.assert_not_awaited()


async def test_monitor_deletes_crons_for_missing_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    client = AsyncMock()
    client.threads.get.side_effect = NotFoundError(
        "not found",
        response=httpx.Response(404, request=httpx.Request("GET", "http://test")),
        body=None,
    )
    delete_crons = AsyncMock()
    monkeypatch.setattr(background_tasks, "_client", lambda: client)
    monkeypatch.setattr(background_tasks, "_delete_crons", delete_crons)

    assert await monitor_background_tasks("thread-1") == {"status": "missing_thread"}
    delete_crons.assert_awaited_once_with("thread-1")
