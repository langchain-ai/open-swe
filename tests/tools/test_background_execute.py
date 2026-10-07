import importlib
import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from langgraph_sdk.errors import NotFoundError

from openswe import background_tasks
from openswe.background_tasks import monitor_background_tasks, reconcile_background_tasks
from openswe.sandboxes import tool_access, tool_routes
from openswe.slack import thinking as slack_thinking
from openswe.tasks import service as task_service
from openswe.tools import threads
from openswe.tools.background_execute import (
    TASK_ROOT,
    _launch_command,
    _runner,
    background_execute,
    control_script,
)
from openswe.utils.background_task_state import update_background_task_state

TOOLS_URL = "https://agent.example.test/dashboard/api/sandbox-tools"
FAKE_CURL = """#!/usr/bin/env python3
import pathlib, sys
root = pathlib.Path(__file__).parent
with (root / "calls").open("a") as handle:
    handle.write(sys.stdin.read())
codes = (root / "codes").read_text().split() if (root / "codes").exists() else []
(root / "codes").write_text("\\n".join(codes[1:]))
print(codes[0] if codes else "204", end="")
"""


@pytest.fixture
def fake_curl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A curl on PATH that records each callback and answers with queued status codes."""
    curl = tmp_path / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("OPEN_SWE_TOOLS_URL", TOOLS_URL)
    return tmp_path


def _run_control(action: str, task_id: str) -> dict:
    result = subprocess.run(
        ["python3", "-c", control_script(action, task_id)],
        capture_output=True,
        check=True,
        text=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("callback", "codes", "calls"),
    [(True, ["503", "000", "204"], 3), (True, ["401"], 1), (False, [], 0)],
)
def test_runner_calls_back_on_completion_until_accepted(
    fake_curl: Path, monkeypatch: pytest.MonkeyPatch, callback: bool, codes: list[str], calls: int
) -> None:
    (fake_curl / "codes").write_text("\n".join(codes))
    module = importlib.import_module("openswe.tools.background_execute")
    monkeypatch.setattr(module, "CALLBACK_RETRY_DELAYS", (0, 0, 0, 0))
    task_id = f"test-{uuid.uuid4().hex}"
    task_dir = Path(TASK_ROOT, task_id)
    task_dir.mkdir(parents=True)
    runner = task_dir / "runner.py"
    runner.write_text(_runner(task_id, "echo hi", 10, callback, owner_thread_id="thread-a"))
    try:
        subprocess.run(["python3", str(runner)], check=True, timeout=10)

        state = json.loads((task_dir / "state.json").read_text())
        assert state["status"] == "completed"
        assert state["owner_thread_id"] == "thread-a"
        url = f"{TOOLS_URL}/background-tasks/{task_id}/complete"
        calls_file = fake_curl / "calls"
        recorded = calls_file.read_text().splitlines() if calls_file.exists() else []
        assert recorded == [f'url = "{url}"'] * calls
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


def test_background_launch_requires_callback_url(monkeypatch: pytest.MonkeyPatch) -> None:
    if Path(tool_access.TOOLS_URL_FILE).exists():
        pytest.skip("this host has a provisioned sandbox tools URL")
    monkeypatch.delenv("OPEN_SWE_TOOLS_URL", raising=False)
    task_id = f"test-{uuid.uuid4().hex}"
    try:
        result = subprocess.run(
            [
                "/bin/sh",
                "-c",
                _launch_command(task_id, "true", 10, callback=True, owner_thread_id="thread-a"),
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        assert result.returncode == 75
        assert not Path(TASK_ROOT, task_id).exists()
    finally:
        shutil.rmtree(Path(TASK_ROOT, task_id), ignore_errors=True)


def test_background_command_returns_while_running_then_caps_output(fake_curl: Path) -> None:
    task_id = f"test-{uuid.uuid4().hex}"
    task_dir = Path(TASK_ROOT, task_id)
    command = "python3 -c \"print('x' * 1200000)\"; sleep .5; echo done"
    try:
        started = time.monotonic()
        launched = subprocess.run(
            [
                "/bin/sh",
                "-c",
                _launch_command(task_id, command, 10, callback=True, owner_thread_id="thread-a"),
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=3,
        )
        assert time.monotonic() - started < 2
        assert json.loads(launched.stdout)["status"] == "running"
        assert json.loads(launched.stdout)["owner_thread_id"] == "thread-a"

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


def test_background_command_timeout_and_stop(fake_curl: Path) -> None:
    for timeout, stop, expected in ((1, False, "timed_out"), (10, True, "stopped")):
        task_id = f"test-{uuid.uuid4().hex}"
        task_dir = Path(TASK_ROOT, task_id)
        try:
            subprocess.run(
                [
                    "/bin/sh",
                    "-c",
                    _launch_command(
                        task_id, "sleep 30", timeout, callback=False, owner_thread_id="thread-a"
                    ),
                ],
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


async def _callback_client(monkeypatch: pytest.MonkeyPatch) -> tuple[httpx.AsyncClient, str]:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-tools-signing-key")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://agent.example.test")
    client = MagicMock()
    client.threads.get = AsyncMock(return_value={"metadata": {"sandbox_id": "sandbox-a"}})
    monkeypatch.setattr(tool_access, "get_client", lambda: client)
    issued = await tool_access.issue_tool_access("thread-a", "sandbox-a")
    assert issued is not None
    app = FastAPI()
    app.include_router(tool_routes.router)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")
    return http, issued[1]


@pytest.mark.parametrize("dispatch_fails", [False, True])
async def test_completion_callback_asks_runner_to_retry_until_delivered(
    monkeypatch: pytest.MonkeyPatch, dispatch_fails: bool
) -> None:
    client = AsyncMock()
    client.threads.get.return_value = {
        "metadata": {
            "sandbox_id": "sandbox-a",
            "sandbox_host_thread_id": "thread-a",
            "running_background_tasks": ["cmd-1"],
        }
    }
    task = {
        "task_id": "cmd-1",
        "owner_thread_id": "thread-b",
        "status": "completed",
        "notification": "pending",
    }
    backend = AsyncMock()
    backend.aexecute.return_value = SimpleNamespace(exit_code=0)
    dispatch = AsyncMock(side_effect=RuntimeError("unavailable") if dispatch_fails else None)
    monkeypatch.setattr(background_tasks, "_client", lambda: client)
    monkeypatch.setattr(background_tasks, "connect_sandbox", AsyncMock(return_value=backend))
    monkeypatch.setattr(background_tasks, "_list_tasks", AsyncMock(return_value=[task]))
    monkeypatch.setattr(background_tasks, "dispatch_agent_run", dispatch)
    http, token = await _callback_client(monkeypatch)
    async with http:
        response = await http.post(
            "/dashboard/api/sandbox-tools/background-tasks/cmd-1/complete",
            headers={tool_access.TOOLS_HEADER: token},
        )
    assert response.status_code == (503 if dispatch_fails else 204)
    assert dispatch.await_args is not None
    assert dispatch.await_args.args[0] == "thread-b"
    assert task["notification"] == ("pending" if dispatch_fails else "done")


@pytest.mark.parametrize(
    ("sandbox_id", "host_id"),
    [("another-sandbox", "thread-a"), ("sandbox-a", "another-host")],
)
async def test_completion_callback_cannot_route_outside_its_shared_sandbox(
    monkeypatch: pytest.MonkeyPatch, sandbox_id: str, host_id: str
) -> None:
    client = AsyncMock()
    client.threads.get.return_value = {
        "metadata": {"sandbox_id": sandbox_id, "sandbox_host_thread_id": host_id}
    }
    monkeypatch.setattr(background_tasks, "_client", lambda: client)
    monkeypatch.setattr(background_tasks, "connect_sandbox", AsyncMock())
    monkeypatch.setattr(
        background_tasks,
        "_list_tasks",
        AsyncMock(return_value=[{"task_id": "cmd-1", "owner_thread_id": "thread-b"}]),
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(background_tasks, "dispatch_agent_run", dispatch)
    http, token = await _callback_client(monkeypatch)
    async with http:
        response = await http.post(
            "/dashboard/api/sandbox-tools/background-tasks/cmd-1/complete",
            headers={tool_access.TOOLS_HEADER: token},
        )
    assert response.status_code == 403
    dispatch.assert_not_awaited()


@pytest.mark.parametrize(
    ("tasks", "status_code"),
    [
        ([{"task_id": "cmd-1", "status": "running"}], 204),
        ([{"task_id": "cmd-1", "status": "completed"}], 409),
        ([{"task_id": "cmd-other", "status": "running"}], 404),
    ],
)
async def test_heartbeat_keeps_the_sandbox_alive_only_for_a_running_task(
    monkeypatch: pytest.MonkeyPatch, tasks: list[dict[str, str]], status_code: int
) -> None:
    create_sandbox = AsyncMock(return_value=object())
    monkeypatch.setattr(background_tasks, "connect_sandbox", create_sandbox)
    monkeypatch.setattr(background_tasks, "_list_tasks", AsyncMock(return_value=tasks))
    http, token = await _callback_client(monkeypatch)
    async with http:
        unauthenticated = await http.post(
            "/dashboard/api/sandbox-tools/background-tasks/cmd-1/heartbeat"
        )
        response = await http.post(
            "/dashboard/api/sandbox-tools/background-tasks/cmd-1/heartbeat",
            headers={tool_access.TOOLS_HEADER: token},
        )
    assert unauthenticated.status_code == 401
    assert response.status_code == status_code
    create_sandbox.assert_awaited_once_with("sandbox-a")


@pytest.mark.parametrize(
    ("status", "has_sandbox", "tracked", "relisted", "deleted"),
    [
        ("missing_sandbox", False, True, [], True),
        ("idle", True, True, [], True),
        ("idle", True, False, [], False),
        ("running", True, True, [], False),
        ("idle", True, True, [{"task_id": "cmd-new", "status": "running"}], False),
    ],
)
async def test_cron_tick_deletes_its_cron_only_once_nothing_is_left(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    has_sandbox: bool,
    tracked: bool,
    relisted: list[dict[str, str]],
    deleted: bool,
) -> None:
    backend = AsyncMock()
    backend.aexecute.return_value = SimpleNamespace(exit_code=0)
    reconciled = background_tasks._Reconciled(
        {"status": status}, backend if has_sandbox else None, tracked
    )
    monkeypatch.setattr(background_tasks, "_reconcile", AsyncMock(return_value=reconciled))
    monkeypatch.setattr(background_tasks, "_list_tasks", AsyncMock(return_value=relisted))
    delete_crons = AsyncMock()
    monkeypatch.setattr(background_tasks, "_delete_crons", delete_crons)

    assert await monitor_background_tasks("thread-1") == {"status": status}

    assert delete_crons.await_count == int(deleted)


async def test_shared_sandbox_monitors_deliver_only_their_own_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: dict[str, dict[str, object]] = {
        "host": {"sandbox_id": "shared"},
        "worker-a": {"sandbox_id": "shared", "sandbox_host_thread_id": "host"},
        "worker-b": {"sandbox_id": "shared", "sandbox_host_thread_id": "host"},
    }
    tasks = [
        {"task_id": "cmd-a", "owner_thread_id": "worker-a", "status": "completed"},
        {"task_id": "cmd-a-running", "owner_thread_id": "worker-a", "status": "running"},
        {"task_id": "cmd-b", "owner_thread_id": "worker-b", "status": "completed"},
        {"task_id": "cmd-legacy", "status": "completed"},
    ]

    async def get(thread_id: str) -> dict[str, object]:
        return {"metadata": dict(stored[thread_id])}

    async def update(thread_id: str, *, metadata: dict[str, object]) -> None:
        stored[thread_id].update(metadata)

    client = AsyncMock()
    client.threads.get.side_effect = get
    client.threads.update.side_effect = update
    backend = AsyncMock()
    backend.aexecute.return_value = SimpleNamespace(exit_code=0)
    dispatch = AsyncMock()
    delete_crons = AsyncMock()
    monkeypatch.setattr(background_tasks, "_client", lambda: client)
    monkeypatch.setattr(background_tasks, "connect_sandbox", AsyncMock(return_value=backend))
    monkeypatch.setattr(background_tasks, "_list_tasks", AsyncMock(return_value=tasks))
    monkeypatch.setattr(background_tasks, "dispatch_agent_run", dispatch)
    monkeypatch.setattr(background_tasks, "_delete_crons", delete_crons)

    assert await monitor_background_tasks("worker-b") == {
        "status": "idle",
        "delivered": 1,
        "pending": 0,
    }
    assert "notification" not in tasks[0]
    assert "notification" not in tasks[3]
    assert not stored["worker-b"].get("running_background_tasks")
    delete_crons.assert_awaited_once_with("worker-b")

    assert await monitor_background_tasks("worker-a") == {
        "status": "running",
        "delivered": 1,
        "pending": 0,
    }
    assert stored["worker-a"]["running_background_tasks"] == ["cmd-a-running"]
    delete_crons.assert_awaited_once_with("worker-b")

    assert await monitor_background_tasks("host") == {
        "status": "idle",
        "delivered": 1,
        "pending": 0,
    }
    assert [call.args[0] for call in dispatch.await_args_list] == ["worker-b", "worker-a", "host"]
    for call, task_id in zip(
        dispatch.await_args_list, ("cmd-b", "cmd-a", "cmd-legacy"), strict=True
    ):
        assert task_id in call.args[1]


@pytest.mark.parametrize(
    ("login", "profile", "enabled"),
    [
        ("alice", {"experimental_background_callbacks": True}, True),
        ("alice", {"experimental_background_callbacks": False}, False),
        ("alice", {"default_model": "openai:test"}, False),
        ("alice", None, False),
        (None, {"experimental_background_callbacks": True}, False),
    ],
)
async def test_callbacks_follow_the_triggering_persons_flag(
    login: str | None, profile: dict[str, object] | None, enabled: bool
) -> None:
    module = importlib.import_module("openswe.tools.background_execute")
    get_profile = AsyncMock(return_value=profile)
    with (
        patch.object(module, "get_profile", get_profile),
        patch.object(
            module.RunConfig, "from_runtime", return_value=SimpleNamespace(github_login=login)
        ),
    ):
        assert await module._uses_completion_callback() is enabled
    if login:
        get_profile.assert_awaited_once_with(login)


@pytest.mark.parametrize("callback", [False, True])
async def test_background_execute_schedules_a_cron_only_without_callbacks(callback: bool) -> None:
    backend = AsyncMock()
    backend.aexecute.return_value = SimpleNamespace(exit_code=0)
    launches = [{"task_id": "cmd-1", "status": "running"}]
    with (
        patch(
            "openswe.tools.background_execute._uses_completion_callback",
            AsyncMock(return_value=callback),
        ),
        patch(
            "openswe.tools.background_execute._current_backend", return_value=("thread-1", backend)
        ),
        patch(
            "openswe.tools.background_execute.execute",
            AsyncMock(side_effect=launches if callback else [{"tasks": []}, *launches]),
        ),
        patch("openswe.tools.background_execute.update_background_task_state", AsyncMock()),
        patch("openswe.background_tasks.ensure_background_task_cron", AsyncMock()) as ensure_cron,
    ):
        result = await background_execute("sleep 10")

    assert result == {"success": True, "task_id": "cmd-1", "status": "running"}
    assert ensure_cron.await_count == int(not callback)


async def test_background_execute_reports_monitor_scheduling_failure() -> None:
    backend = AsyncMock()
    backend.aexecute.return_value = SimpleNamespace(exit_code=0)

    with (
        patch(
            "openswe.tools.background_execute._uses_completion_callback",
            AsyncMock(return_value=False),
        ),
        patch("openswe.tools.background_execute.update_background_task_state", AsyncMock()),
        patch(
            "openswe.tools.background_execute._current_backend", return_value=("thread-1", backend)
        ),
        patch(
            "openswe.tools.background_execute.execute",
            AsyncMock(side_effect=[{"tasks": []}, {"task_id": "task-1", "status": "running"}]),
        ),
        patch(
            "openswe.background_tasks.ensure_background_task_cron",
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


@pytest.mark.parametrize(
    ("tracking_failure", "worker"), [(False, False), (True, False), (False, True)]
)
async def test_reconcile_enqueues_one_claimed_completion(
    monkeypatch: pytest.MonkeyPatch, tracking_failure: bool, worker: bool
) -> None:
    task = {
        "task_id": "task-1",
        "owner_thread_id": "thread-1",
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
    if worker:
        client.threads.get.return_value["metadata"].update(
            source="dashboard",
            source_context={},
            task_id=str(uuid.uuid4()),
            sandbox_host_thread_id="coordinator",
            owner_type="user",
            owner_login="worker-owner",
        )
        monkeypatch.setattr(task_service, "langgraph_client", lambda: client)
        monkeypatch.setattr(task_service, "enforce_github_login_gate", AsyncMock())
        monkeypatch.setattr(
            task_service.ThreadMetadata,
            "owner",
            AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4(), github_login="worker-owner")),
        )
        monkeypatch.setattr(task_service, "get_profile", AsyncMock(return_value={}))
        monkeypatch.setattr(task_service, "resolve_run_email", AsyncMock(return_value=None))
        monkeypatch.setattr(threads, "enforce_github_login_gate", AsyncMock())

    with (
        patch("openswe.background_tasks._client", return_value=client),
        patch(
            "openswe.background_tasks.update_background_task_state",
            AsyncMock(
                side_effect=RuntimeError("metadata unavailable") if tracking_failure else None
            ),
        ),
        patch("openswe.background_tasks.connect_sandbox", AsyncMock(return_value=backend)),
        patch("openswe.background_tasks._list_tasks", AsyncMock(return_value=[task])),
        patch("openswe.background_tasks._claim", AsyncMock(return_value=True)),
        patch("openswe.background_tasks._mark_delivered", AsyncMock()),
        patch("openswe.background_tasks.dispatch_agent_run", AsyncMock()) as dispatch,
    ):
        result = await reconcile_background_tasks("thread-1")

    assert result == {"status": "idle", "delivered": 1, "pending": 0}
    dispatch.assert_awaited_once()
    assert dispatch.await_args is not None
    configurable = dispatch.await_args.args[2]
    source = "dashboard" if worker else "slack"
    assert configurable["source"] == source
    assert configurable["background_task_completion"] is True
    assert dispatch.await_args.kwargs["source"] == source
    if worker:
        config = {"configurable": configurable}
        monkeypatch.setattr(threads, "get_config", lambda: config)
        monkeypatch.setattr(task_service, "get_config", lambda: config)
        assert await task_service.Actor.resolve({}) == task_service.Actor(
            thread_id="thread-1", login="worker-owner"
        )
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


async def test_reconcile_releases_claim_when_dispatch_fails() -> None:
    task = {"task_id": "task-1", "status": "completed", "notification": "pending"}
    client = AsyncMock()
    client.threads.get.return_value = {
        "metadata": {"sandbox_id": "sandbox-1", "running_background_tasks": []}
    }

    with (
        patch("openswe.background_tasks._client", return_value=client),
        patch("openswe.background_tasks.connect_sandbox", AsyncMock(return_value=AsyncMock())),
        patch("openswe.background_tasks._list_tasks", AsyncMock(return_value=[task])),
        patch("openswe.background_tasks._claim", AsyncMock(return_value=True)),
        patch("openswe.background_tasks._unclaim", AsyncMock()) as unclaim,
        patch("openswe.background_tasks._mark_delivered", AsyncMock()) as mark_delivered,
        patch(
            "openswe.background_tasks.dispatch_agent_run",
            AsyncMock(side_effect=RuntimeError("langgraph unavailable")),
        ),
    ):
        result = await reconcile_background_tasks("thread-1")

    assert result == {"status": "running", "delivered": 0, "pending": 1}
    unclaim.assert_awaited_once()
    mark_delivered.assert_not_awaited()


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
async def test_reconcile_uses_fresh_state_for_concurrent_launch(
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
    monkeypatch.setattr(background_tasks, "connect_sandbox", AsyncMock(return_value=backend))
    monkeypatch.setattr(background_tasks, "_list_tasks", AsyncMock(return_value=[]))
    set_status = AsyncMock()
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)

    await reconcile_background_tasks("thread-1")

    set_status.assert_awaited_once_with("C1", "1.0", "Waiting for background tasks…")
    assert client.threads.get.await_count == (3 if tracking_failure else 2)
    if tracking_failure:
        client.threads.update.assert_not_awaited()
    else:
        client.threads.update.assert_awaited_once_with(
            "thread-1", metadata={"running_background_tasks": ["cmd-concurrent"]}
        )


@pytest.mark.parametrize("slack", [False, True])
@pytest.mark.parametrize("run_active", [False, True])
async def test_reconcile_refreshes_task_state_after_completion_dispatch(
    monkeypatch: pytest.MonkeyPatch, slack: bool, run_active: bool
) -> None:
    from openswe import dispatch, thread_feedback

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
    monkeypatch.setattr(background_tasks, "connect_sandbox", AsyncMock(return_value=backend))
    monkeypatch.setattr(
        background_tasks,
        "_list_tasks",
        AsyncMock(
            return_value=[{"task_id": "cmd-old", "status": "completed", "notification": "pending"}]
        ),
    )
    monkeypatch.setattr(background_tasks, "_claim", AsyncMock(return_value=True))
    monkeypatch.setattr(background_tasks, "_mark_delivered", AsyncMock())
    set_status = AsyncMock()
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)

    result = await reconcile_background_tasks("thread-1")

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


@pytest.mark.parametrize("launch_fails", [False, True])
async def test_callback_launch_tracks_the_task_before_its_runner_can_call_back(
    launch_fails: bool,
) -> None:
    tracked: set[str] = set()

    async def update(
        client: object,
        thread_id: str,
        *,
        running: Sequence[str] = (),
        finished: Sequence[str] = (),
    ) -> None:
        tracked.update(running)
        tracked.difference_update(finished)

    async def launch(backend: object, command: str) -> dict[str, str]:
        assert len(tracked) == 1, "the runner starts inside this call"
        if launch_fails:
            raise RuntimeError("active task limit reached")
        return {"task_id": next(iter(tracked)), "status": "running"}

    with (
        patch(
            "openswe.tools.background_execute._uses_completion_callback",
            AsyncMock(return_value=True),
        ),
        patch(
            "openswe.tools.background_execute._current_backend", return_value=("thread-1", object())
        ),
        patch("openswe.tools.background_execute.execute", side_effect=launch),
        patch("openswe.tools.background_execute.update_background_task_state", side_effect=update),
        patch("openswe.tools.background_execute.langgraph_client"),
    ):
        result = await background_execute("true")

    assert result["success"] is not launch_fails
    assert bool(tracked) is not launch_fails


@pytest.mark.parametrize("replaced", [False, True])
async def test_reattach_reconciles_tracked_tasks_even_on_a_replacement_sandbox(
    replaced: bool,
) -> None:
    import asyncio

    from openswe.sandboxes.lifecycle import SANDBOX_BACKENDS, ensure_sandbox_for_thread
    from openswe.sandboxes.providers.registry import SandboxGoneError

    SANDBOX_BACKENDS.clear()
    sandbox = MagicMock()
    sandbox.id = "sandbox-replacement" if replaced else "sandbox-old"
    reconcile = AsyncMock()
    with (
        patch(
            "openswe.sandboxes.lifecycle.get_sandbox_metadata",
            AsyncMock(
                return_value={"sandbox_id": "sandbox-old", "running_background_tasks": ["cmd-1"]}
            ),
        ),
        patch(
            "openswe.sandboxes.lifecycle._connect_existing_sandbox",
            AsyncMock(
                side_effect=SandboxGoneError("gone") if replaced else None, return_value=sandbox
            ),
        ),
        patch(
            "openswe.sandboxes.lifecycle._create_sandbox_with_proxy",
            AsyncMock(return_value=sandbox),
        ),
        patch("openswe.sandboxes.lifecycle.client.threads.update", AsyncMock()),
        patch(
            "openswe.sandboxes.lifecycle.thread_token_repositories", AsyncMock(return_value=None)
        ),
        patch.object(background_tasks, "reconcile_background_tasks", reconcile),
    ):
        await ensure_sandbox_for_thread("thread-1")
        await asyncio.sleep(0)

    reconcile.assert_awaited_once_with("thread-1")
    SANDBOX_BACKENDS.clear()
