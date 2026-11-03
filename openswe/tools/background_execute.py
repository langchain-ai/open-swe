"""Non-blocking command execution in the thread sandbox."""

import logging
import shlex
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue

from openswe.dashboard.profiles import get_profile
from openswe.run_config import RunConfig
from openswe.sandboxes.scripts import SandboxScript, SandboxScriptError
from openswe.sandboxes.state import SandboxBackendProxy, get_or_create_sandbox_backend_proxy
from openswe.sandboxes.tool_access import TOOLS_URL_ENV, TOOLS_URL_FILE
from openswe.utils.background_task_state import update_background_task_state
from openswe.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

TASK_ROOT = "/tmp/open-swe-background-tasks"
# Task ids are prefixed so the one poll tool can route an id to the thing that
# knows how to read it, without the caller having to say which kind it is.
TASK_PREFIX = "cmd"
TASK_KIND = "sandbox_command"
DEFAULT_TIMEOUT_SECONDS = 3600
MAX_TIMEOUT_SECONDS = 86_400
# The script enforces it too, under its launch lock.
MAX_ACTIVE_TASKS = 4


# Run from a copy in TASK_ROOT; see openswe/sandbox_scripts/background_task.py.
SCRIPT = SandboxScript("background_task.py", TASK_ROOT)


async def execute(backend: Any, action: str, **fields: JsonValue) -> dict[str, JsonValue]:
    """Run one action of the background-task script in ``backend``'s sandbox."""
    try:
        return await SCRIPT.run(backend, {"action": action, **fields})
    except SandboxScriptError as exc:
        raise RuntimeError(str(exc)) from exc


async def list_tasks(backend: Any, *, required: bool = False) -> list[dict[str, JsonValue]]:
    """Every background command in ``backend``'s sandbox, whichever thread launched it."""
    tasks = (await execute(backend, "list")).get("tasks")
    if isinstance(tasks, list):
        return [task for task in tasks if isinstance(task, dict)]
    if required:
        raise RuntimeError("Background-task list could not be read")
    return []


async def _launch(
    backend: Any, task_id: str, command: str, timeout: int, *, callback: bool, owner_thread_id: str
) -> dict[str, JsonValue]:
    return await execute(
        backend,
        "launch",
        task_id=task_id,
        command=command,
        timeout=timeout,
        callback=callback,
        owner_thread_id=owner_thread_id,
        tools_url_env=TOOLS_URL_ENV,
        tools_url_file=TOOLS_URL_FILE,
    )


def current_backend() -> tuple[str, SandboxBackendProxy]:
    thread_id = RunConfig.from_runtime().thread_id
    if not isinstance(thread_id, str) or not thread_id:
        raise RuntimeError("No thread_id in current run config")
    return thread_id, get_or_create_sandbox_backend_proxy(thread_id)


async def background_execute(
    command: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Implement the `background_execute` tool."""
    if not command.strip():
        return {"success": False, "error": "command must not be empty"}
    if not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        return {"success": False, "error": f"timeout must be between 1 and {MAX_TIMEOUT_SECONDS}s"}
    try:
        thread_id, backend = current_backend()
        if await uses_completion_callback():
            return await _launch_with_callback(thread_id, backend, command, timeout)
        return await _launch_with_cron(thread_id, backend, command, timeout)
    except Exception as exc:
        logger.exception("Failed to start background command")
        return {"success": False, "error": str(exc)}


class _CallbackFlag(BaseModel):
    model_config = ConfigDict(extra="ignore")

    experimental_background_callbacks: bool | None = None


async def uses_completion_callback() -> bool:
    """The triggering person's opt-in to sandbox completion callbacks instead of polling crons."""
    login = RunConfig.from_runtime().github_login
    if not login:
        return False
    profile = await get_profile(login)
    return bool(profile and _CallbackFlag.model_validate(profile).experimental_background_callbacks)


async def _track(thread_id: str, task_id: str, *, running: bool = True) -> None:
    ids = [task_id]
    try:
        await update_background_task_state(
            langgraph_client(),
            thread_id,
            running=ids if running else (),
            finished=() if running else ids,
        )
    except Exception:
        logger.warning(
            "Could not track background command", extra={"task_id": task_id}, exc_info=True
        )


async def _launch_with_callback(
    thread_id: str, backend: Any, command: str, timeout: int
) -> dict[str, Any]:
    task_id = f"{TASK_PREFIX}-{uuid.uuid4()}"
    # Tracked first: a fast command's completion callback can reconcile before launch returns.
    await _track(thread_id, task_id)
    try:
        state = await _launch(
            backend, task_id, command, timeout, callback=True, owner_thread_id=thread_id
        )
    except Exception:
        await _track(thread_id, task_id, running=False)
        raise
    return {"success": True, **state}


async def _launch_with_cron(
    thread_id: str, backend: Any, command: str, timeout: int
) -> dict[str, Any]:
    active = sum(task.get("status") == "running" for task in await list_tasks(backend))
    if active >= MAX_ACTIVE_TASKS:
        return {"success": False, "error": "active task limit reached"}
    from openswe.background_tasks import MONITOR_LOCK, ensure_background_task_polling

    wait_for_monitor = f"while [ -d {shlex.quote(MONITOR_LOCK)} ]; do sleep .1; done"
    wait = await backend.aexecute(wait_for_monitor, timeout=15)
    if getattr(wait, "exit_code", None) != 0:
        raise RuntimeError("background-task monitor is busy")
    task_id = f"{TASK_PREFIX}-{uuid.uuid4()}"
    state = await _launch(
        backend, task_id, command, timeout, callback=False, owner_thread_id=thread_id
    )
    await _track(thread_id, task_id)
    wait = await backend.aexecute(wait_for_monitor, timeout=15)
    if getattr(wait, "exit_code", None) != 0:
        return {
            "success": False,
            **state,
            "error": "command started, but automatic completion monitoring is busy",
        }
    try:
        await ensure_background_task_polling(thread_id)
    except Exception:
        logger.warning("Failed to schedule background-task monitor", exc_info=True)
        return {
            "success": False,
            **state,
            "error": "command started, but automatic completion monitoring could not be scheduled",
        }
    return {"success": True, **state}


def owns_task(task_id: str) -> bool:
    """Whether this id names a sandbox command.

    Unprefixed ids are ours too: they are what `background_execute` minted
    before task kinds existed, and such a task can still be running in a
    sandbox that predates this code.
    """
    from openswe.workspaces.refresh import owns_task as refresh_owns_task

    return not refresh_owns_task(task_id)


async def _control(action: str, task_id: str | None) -> dict[str, JsonValue]:
    _, backend = current_backend()
    return await execute(backend, action, task_id=task_id)


async def task_status(task_id: str) -> dict[str, Any]:
    state = await _control("status", task_id)
    return {"kind": TASK_KIND, "output_source": "sandbox", **state}


async def task_stop(task_id: str) -> dict[str, Any]:
    state = await _control("stop", task_id)
    return {"kind": TASK_KIND, **state}


async def task_list() -> list[dict[str, Any]]:
    _, backend = current_backend()
    return [{"kind": TASK_KIND, **task} for task in await list_tasks(backend)]
