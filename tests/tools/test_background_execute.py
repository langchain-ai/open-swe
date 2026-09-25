import importlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from agent import background_tasks
from agent.background_tasks import monitor_background_tasks, reconcile_background_tasks
from agent.sandboxes import tool_access, tool_routes
from agent.slack import thinking as slack_thinking
from agent.tools.background_execute import (
    TASK_ROOT,
    _launch_command,
    _runner,
    background_execute,
    control_script,
)
from agent.utils.background_task_state import update_background_task_state

# _launch_command refuses to run without setsid, which macOS does not ship; the
# sandbox these tasks run in is always Linux.
requires_setsid = pytest.mark.skipif(
    shutil.which("setsid") is None, reason="setsid is unavailable on this host"
)
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
    module = importlib.import_module("agent.tools.background_execute")
    monkeypatch.setattr(module, "CALLBACK_RETRY_DELAYS", (0, 0, 0, 0))
    task_id = f"test-{uuid.uuid4().hex}"
    task_dir = Path(TASK_ROOT, task_id)
    task_dir.mkdir(parents=True)
    runner = task_dir / "runner.py"
    runner.write_text(_runner(task_id, "echo hi", 10, callback))
    try:
        subprocess.run(["python3", str(runner)], check=True, timeout=10)

        assert json.loads((task_dir / "state.json").read_text())["status"] == "completed"
        url = f"{TOOLS_URL}/background-tasks/{task_id}/complete"
        calls_file = fake_curl / "calls"
        recorded = calls_file.read_text().splitlines() if calls_file.exists() else []
        assert recorded == [f'url = "{url}"'] * calls
    finally:
        shutil.rmtree(task_dir, ignore_errors=True)


@requires_setsid
def test_background_launch_requires_callback_url(monkeypatch: pytest.MonkeyPatch) -> None:
    if Path(tool_access.TOOLS_URL_FILE).exists():
        pytest.skip("this host has a provisioned sandbox tools URL")
    monkeypatch.delenv("OPEN_SWE_TOOLS_URL", raising=False)
    task_id = f"test-{uuid.uuid4().hex}"
    try:
        result = subprocess.run(
            ["/bin/sh", "-c", _launch_command(task_id, "true", 10, callback=True)],
            capture_output=True,
            text=True,
            timeout=3,
        )
        assert result.returncode == 75
        assert not Path(TASK_ROOT, task_id).exists()
    finally:
        shutil.rmtree(Path(TASK_ROOT, task_id), ignore_errors=True)


@requires_setsid
def test_background_command_returns_while_running_then_caps_output(fake_curl: Path) -> None:
    task_id = f"test-{uuid.uuid4().hex}"
    task_dir = Path(TASK_ROOT, task_id)
    command = "python3 -c \"print('x' * 1200000)\"; sleep .5; echo done"
    try:
        started = time.monotonic()
        launched = subprocess.run(
            ["/bin/sh", "-c", _launch_command(task_id, command, 10, callback=True)],
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
def test_background_command_active_limit(fake_curl: Path) -> None:
    task_ids = [f"test-{uuid.uuid4().hex}" for _ in range(4)]
    try:
        for task_id in task_ids:
            task_dir = Path(TASK_ROOT, task_id)
            task_dir.mkdir(parents=True)
            task_dir.joinpath("state.json").write_text('{"status": "running"}')
        result = subprocess.run(
            [
                "/bin/sh",
                "-c",
                _launch_command(f"test-{uuid.uuid4().hex}", "true", 10, callback=True),
            ],
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
def test_background_command_timeout_and_stop(fake_curl: Path) -> None:
    for timeout, stop, expected in ((1, False, "timed_out"), (10, True, "stopped")):
        task_id = f"test-{uuid.uuid4().hex}"
        task_dir = Path(TASK_ROOT, task_id)
        try:
            subprocess.run(
                ["/bin/sh", "-c", _launch_command(task_id, "sleep 30", timeout, callback=False)],
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


def test_dispatch_config_carries_new_workspace_key() -> None:
    configurable = background_tasks._dispatch_config(
        {"source": "slack", "workspace": "oss"}, "thread-1"
    )
    assert configurable["workspace"] == "oss"
    assert configurable["environment"] == "oss"


def test_dispatch_config_falls_back_to_legacy_environment_key() -> None:
    configurable = background_tasks._dispatch_config(
        {"source": "slack", "environment": "old"}, "thread-1"
    )
    assert configurable["workspace"] == "old"
    assert configurable["environment"] == "old"


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


@pytest.mark.parametrize(("pending", "status_code"), [(0, 204), (1, 503)])
async def test_completion_callback_asks_runner_to_retry_until_delivered(
    monkeypatch: pytest.MonkeyPatch, pending: int, status_code: int
) -> None:
    reconcile = AsyncMock(return_value={"status": "idle", "delivered": 0, "pending": pending})
    monkeypatch.setattr(background_tasks, "reconcile_background_tasks", reconcile)
    http, token = await _callback_client(monkeypatch)
    async with http:
        response = await http.post(
            "/dashboard/api/sandbox-tools/background-tasks/cmd-1/complete",
            headers={tool_access.TOOLS_HEADER: token},
        )
    assert response.status_code == status_code
    reconcile.assert_awaited_once_with("thread-a")


async def test_heartbeat_callback_touches_the_callers_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keep_alive = AsyncMock()
    monkeypatch.setattr(background_tasks, "keep_sandbox_alive", keep_alive)
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
    assert response.status_code == 204
    keep_alive.assert_awaited_once_with("sandbox-a")


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


async def test_background_task_cron_search_uses_metadata_not_graph_name() -> None:
    client = AsyncMock()
    client.crons.search.return_value = []
    client.crons.create.return_value = {"cron_id": "cron-1"}

    with patch("agent.background_tasks._client", return_value=client):
        cron_id = await background_tasks.ensure_background_task_cron("thread-1")

    assert cron_id == "cron-1"
    client.crons.search.assert_awaited_once_with(
        metadata={"kind": "background_tasks", "agent_thread_id": "thread-1"}, limit=10
    )
    assert client.crons.create.await_args.args == ("scheduler",)


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
    module = importlib.import_module("agent.tools.background_execute")
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
            "agent.tools.background_execute._uses_completion_callback",
            AsyncMock(return_value=callback),
        ),
        patch(
            "agent.tools.background_execute._current_backend", return_value=("thread-1", backend)
        ),
        patch(
            "agent.tools.background_execute.execute",
            AsyncMock(side_effect=launches if callback else [{"tasks": []}, *launches]),
        ),
        patch("agent.tools.background_execute.update_background_task_state", AsyncMock()),
        patch("agent.background_tasks.ensure_background_task_cron", AsyncMock()) as ensure_cron,
    ):
        result = await background_execute("sleep 10")

    assert result == {"success": True, "task_id": "cmd-1", "status": "running"}
    assert ensure_cron.await_count == int(not callback)


async def test_background_execute_reports_monitor_scheduling_failure() -> None:
    backend = AsyncMock()
    backend.aexecute.return_value = SimpleNamespace(exit_code=0)

    with (
        patch(
            "agent.tools.background_execute._uses_completion_callback",
            AsyncMock(return_value=False),
        ),
        patch("agent.tools.background_execute.update_background_task_state", AsyncMock()),
        patch(
            "agent.tools.background_execute._current_backend", return_value=("thread-1", backend)
        ),
        patch(
            "agent.tools.background_execute.execute",
            AsyncMock(side_effect=[{"tasks": []}, {"task_id": "task-1", "status": "running"}]),
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


def _cron(cron_id: str, thread_id: str, metadata: dict[str, str]) -> dict[str, object]:
    return {
        "cron_id": cron_id,
        "metadata": metadata,
        "payload": {"input": {"task": "background_tasks", "thread_id": thread_id}},
    }


async def test_delete_crons_removes_legacy_crons_matched_by_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tagged = _cron(
        "tagged", "thread-1", {"kind": "background_tasks", "agent_thread_id": "thread-1"}
    )
    legacy = [
        _cron("legacy-1", "thread-1", {"kind": "background_tasks"}),
        _cron("legacy-2", "thread-1", {"kind": "background_tasks"}),
        _cron("other-thread", "thread-2", {"kind": "background_tasks"}),
    ]
    monkeypatch.setattr(background_tasks, "_CRON_PAGE_SIZE", 2)
    scan = [tagged, *legacy]
    client = AsyncMock()

    async def search(*, metadata: dict[str, str], limit: int, offset: int = 0) -> list[object]:
        if "agent_thread_id" in metadata:
            return [tagged]
        return scan[offset : offset + limit]

    client.crons.search.side_effect = search

    with patch("agent.background_tasks._client", return_value=client):
        await background_tasks._delete_crons("thread-1")

    assert [c.args[0] for c in client.crons.delete.await_args_list] == [
        "tagged",
        "legacy-1",
        "legacy-2",
    ]


@pytest.mark.parametrize("tracking_failure", [False, True])
async def test_reconcile_enqueues_one_claimed_completion(tracking_failure: bool) -> None:
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
        patch("agent.background_tasks._list_tasks", AsyncMock(return_value=[task])),
        patch("agent.background_tasks._claim", AsyncMock(return_value=True)),
        patch("agent.background_tasks._mark_delivered", AsyncMock()),
        patch("agent.background_tasks.dispatch_agent_run", AsyncMock()) as dispatch,
    ):
        result = await reconcile_background_tasks("thread-1")

    assert result == {"status": "idle", "delivered": 1, "pending": 0}
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


async def test_reconcile_releases_claim_when_dispatch_fails() -> None:
    task = {"task_id": "task-1", "status": "completed", "notification": "pending"}
    client = AsyncMock()
    client.threads.get.return_value = {
        "metadata": {"sandbox_id": "sandbox-1", "running_background_tasks": []}
    }

    with (
        patch("agent.background_tasks._client", return_value=client),
        patch("agent.background_tasks.create_sandbox", AsyncMock(return_value=AsyncMock())),
        patch("agent.background_tasks._list_tasks", AsyncMock(return_value=[task])),
        patch("agent.background_tasks._claim", AsyncMock(return_value=True)),
        patch("agent.background_tasks._unclaim", AsyncMock()) as unclaim,
        patch("agent.background_tasks._mark_delivered", AsyncMock()) as mark_delivered,
        patch(
            "agent.background_tasks.dispatch_agent_run",
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


@pytest.mark.parametrize("slack", [False, True])
@pytest.mark.parametrize(
    "status", ["running", "completed", "failed", "timed_out", "stopped", "lost", "missing"]
)
async def test_reconcile_settles_background_waiting_status(status: str, slack: bool) -> None:
    task = {"task_id": "cmd-1", "status": status, "notification": "done"}
    client = AsyncMock()
    metadata: dict[str, object] = {
        "sandbox_id": "sandbox-1",
        "running_background_tasks": ["cmd-1"],
    }
    if slack:
        metadata["source_context"] = {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}}
    client.threads.get.return_value = {"metadata": metadata}
    client.runs.list.return_value = []
    backend = AsyncMock()
    backend.aexecute.return_value = SimpleNamespace(exit_code=0)
    with (
        patch("agent.background_tasks._client", return_value=client),
        patch("agent.background_tasks.create_sandbox", AsyncMock(return_value=backend)),
        patch(
            "agent.background_tasks._list_tasks",
            AsyncMock(return_value=[] if status == "missing" else [task]),
        ),
        patch.object(slack_thinking, "set_slack_thread_status", AsyncMock()) as set_status,
    ):
        await reconcile_background_tasks("thread-1")
    assert client.threads.get.await_count == (1 if status == "running" else 2)
    if status == "running":
        client.threads.update.assert_not_awaited()
    else:
        client.threads.update.assert_awaited_once_with(
            "thread-1", metadata={"running_background_tasks": []}
        )
    if slack:
        set_status.assert_awaited_once_with(
            "C1", "1.0", "Waiting for background tasks…" if status == "running" else ""
        )
    else:
        set_status.assert_not_awaited()
        client.runs.list.assert_not_awaited()
        client.store.get_item.assert_not_awaited()


async def test_background_launch_registers_running_task() -> None:
    backend = AsyncMock()
    backend.aexecute.return_value = SimpleNamespace(exit_code=0)
    with (
        patch(
            "agent.tools.background_execute._uses_completion_callback",
            AsyncMock(return_value=True),
        ),
        patch(
            "agent.tools.background_execute._current_backend", return_value=("thread-1", backend)
        ),
        patch(
            "agent.tools.background_execute.execute",
            AsyncMock(return_value={"task_id": "cmd-1", "status": "running"}),
        ),
        patch("agent.tools.background_execute.update_background_task_state", AsyncMock()) as update,
    ):
        result = await background_execute("sleep 10")
    assert result["success"] is True
    assert update.await_args.args[1] == "thread-1"
    assert len(update.await_args.kwargs["running"]) == 1


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
    monkeypatch.setattr(background_tasks, "create_sandbox", AsyncMock(return_value=backend))
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


@pytest.mark.parametrize("tracked", [False, True])
async def test_missing_sandbox_resets_tasks_without_redundant_reads(
    monkeypatch: pytest.MonkeyPatch, tracked: bool
) -> None:
    client = AsyncMock()
    client.threads.get.return_value = {
        "metadata": {
            "running_background_tasks": ["cmd-1"] if tracked else [],
            "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}},
        }
    }
    client.runs.list.return_value = []
    monkeypatch.setattr(background_tasks, "_client", lambda: client)
    set_status = AsyncMock()
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)

    assert await reconcile_background_tasks("thread-1") == {"status": "missing_sandbox"}

    assert client.threads.get.await_count == (2 if tracked else 1)
    assert client.threads.update.await_count == int(tracked)
    set_status.assert_awaited_once_with("C1", "1.0", "")


@pytest.mark.parametrize("slack", [False, True])
@pytest.mark.parametrize("run_active", [False, True])
async def test_reconcile_refreshes_task_state_after_completion_dispatch(
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
