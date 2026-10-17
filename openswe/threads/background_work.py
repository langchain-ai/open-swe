"""The sandbox commands and monitors running for a thread, for the dashboard."""

import logging

from fastapi import HTTPException
from pydantic import BaseModel

from openswe.monitors.sandbox import MonitorState, SandboxMonitors
from openswe.threads.access import _readable_thread_metadata
from openswe.threads.diffs import connect_sandbox
from openswe.threads.summary import _assert_thread_promptable
from openswe.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY, RUNNING_MONITORS_KEY

logger = logging.getLogger(__name__)


class BackgroundCommand(BaseModel):
    task_id: str
    command: str | None = None
    status: str
    started_at: float | None = None


class BackgroundWork(BaseModel):
    commands: list[BackgroundCommand] = []
    monitors: list[MonitorState] = []


def _tracked(metadata: dict[str, object], key: str) -> bool:
    value = metadata.get(key)
    return isinstance(value, list) and bool(value)


async def get_dashboard_thread_background_work(
    thread_id: str, login: str, *, email: str | None = None
) -> BackgroundWork:
    """What is running in the thread's sandbox on its behalf.

    The sandbox is read only while the thread's metadata says something runs,
    so opening an idle thread never wakes a stopped sandbox.
    """
    from openswe.background_tasks import thread_tasks

    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    # Commands and their output are shell-equivalent, as in the terminal and files.
    _assert_thread_promptable(metadata, login)
    commands_running = _tracked(metadata, RUNNING_BACKGROUND_TASKS_KEY)
    monitors_running = _tracked(metadata, RUNNING_MONITORS_KEY)
    sandbox_id = metadata.get("sandbox_id")
    if not (commands_running or monitors_running) or not isinstance(sandbox_id, str):
        return BackgroundWork()
    try:
        sandbox = await connect_sandbox(sandbox_id, thread_id=thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Could not connect to sandbox for background work", extra={"sandbox": sandbox_id}
        )
        raise HTTPException(503, "Could not connect to the workspace.") from exc
    work = BackgroundWork()
    if commands_running:
        work.commands = [
            BackgroundCommand.model_validate(task)
            for task in await thread_tasks(sandbox, thread_id, metadata)
            if task.get("status") == "running"
        ]
    if monitors_running:
        work.monitors = [
            monitor
            for item in await SandboxMonitors(sandbox, thread_id).listing()
            if (monitor := MonitorState.model_validate(item)).status == "running"
        ]
    return work
