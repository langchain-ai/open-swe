"""One poll for every kind of background work.

Two things run in the background and both take long enough that the model has to
be able to ask "how far has it got": a command launched with
``background_execute``, and a workspace refresh started by
``refresh_workspace_start`` or the nightly cron. They keep their state in
different places — a command's in the thread sandbox's own filesystem, a
refresh's on the workspace record plus a live trace on its builder — so each
kind owns a provider that knows how to read it, and this tool routes by the
task id's prefix.

Both report the same fields: ``status``, ``started_at``/``finished_at``,
``output``, and where that output came from. A refresh adds ``steps``, because a
rebuild is minutes to an hour of setup script and a single pending status cannot
tell slow from wedged.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal, NamedTuple

from agent.run_config import RunConfig
from agent.tools.admin_gate import require_admin
from agent.workspaces import refresh as workspace_refresh

logger = logging.getLogger(__name__)

DEFAULT_WAIT_SECONDS = 120
MAX_WAIT_SECONDS = 300
STATUS_READ_THRESHOLD = 5
TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "stopped", "timed_out", "lost", "stop_requested"}
)
_STATUS_READS: dict[tuple[str, str], int] = {}


class _Provider(NamedTuple):
    name: str
    owns: Callable[[str], bool]
    status: Callable[[str], Awaitable[dict[str, Any]]]
    stop: Callable[[str], Awaitable[dict[str, Any]]]
    list_all: Callable[[], Awaitable[list[dict[str, Any]]]]
    # Refreshes are workspace-wide: a `stop` cancels a rebuild everyone depends
    # on, and a `bash -x` trace expands arguments. Commands are the calling
    # thread's own, in its own sandbox, so they need no such gate.
    admin_only: bool


def _providers() -> tuple[_Provider, ...]:
    """Refresh first: the command provider claims every id it does not recognise."""
    from agent.tools.background_execute import (
        owns_task,
        task_list,
        task_status,
        task_stop,
    )

    return (
        _Provider(
            "workspace refresh",
            workspace_refresh.owns_task,
            workspace_refresh.task_status,
            workspace_refresh.task_stop,
            workspace_refresh.task_list,
            admin_only=True,
        ),
        _Provider(
            "sandbox command", owns_task, task_status, task_stop, task_list, admin_only=False
        ),
    )


async def background_task(
    action: Literal["status", "list", "stop", "wait"],
    task_id: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Implement the `background_task` tool."""
    if action in {"status", "stop", "wait"} and not task_id:
        return {"success": False, "error": f"task_id is required for {action}"}
    try:
        if action == "list":
            return {"success": True, "tasks": await _list_all()}
        assert task_id is not None
        provider = next(p for p in _providers() if p.owns(task_id))
        if provider.admin_only and (denied := await require_admin(f"read {provider.name} tasks")):
            return {"success": False, "error": denied}
        if action == "wait":
            return {"success": True, **await _wait(provider, task_id, timeout)}
        result = await (provider.status if action == "status" else provider.stop)(task_id)
        if action == "status":
            result = _throttle_status(task_id, result)
        return {"success": True, **result}
    except Exception as exc:
        logger.warning("background_task %s failed", action, exc_info=True)
        return {"success": False, "error": str(exc)}


def _run_identity() -> str | None:
    config = RunConfig.from_runtime()
    return config.run_id or config.invocation_id or config.thread_id


def _throttle_status(task_id: str, result: dict[str, Any]) -> dict[str, Any]:
    if result.get("status") != "running":
        return result
    identity = _run_identity()
    if identity is None:
        return result
    key = (identity, task_id)
    reads = _STATUS_READS.get(key, 0) + 1
    _STATUS_READS[key] = reads
    if reads <= STATUS_READ_THRESHOLD:
        return result
    return {
        **result,
        "guidance": (
            'This task is still running. Use background_task(action="wait", '
            "task_id=..., timeout=...) or end the turn and rely on the automatic "
            "completion notification instead of reading status again."
        ),
    }


async def _wait(provider: _Provider, task_id: str, timeout: float | None) -> dict[str, Any]:
    if timeout is None:
        timeout = DEFAULT_WAIT_SECONDS
    if timeout < 0:
        raise ValueError("timeout must be non-negative")
    timeout = min(timeout, MAX_WAIT_SECONDS)
    started = time.monotonic()
    deadline = started + timeout
    latest = await provider.status(task_id)
    if latest.get("status") != "running":
        return {**latest, "waited_seconds": 0, "timed_out": False}
    while time.monotonic() < deadline:
        await asyncio.sleep(min(1, deadline - time.monotonic()))
        latest = await provider.status(task_id)
        if latest.get("status") in TERMINAL_STATUSES or latest.get("status") != "running":
            return {
                **latest,
                "waited_seconds": round(time.monotonic() - started, 2),
                "timed_out": False,
            }
    return {
        **latest,
        "waited_seconds": round(time.monotonic() - started, 2),
        "timed_out": True,
    }


async def _list_all() -> list[dict[str, Any]]:
    """Every task from every provider.

    One provider failing must not blank the listing: a thread with no sandbox
    bound cannot list commands, but its workspace refreshes are still visible.
    """
    tasks: list[dict[str, Any]] = []
    for provider in _providers():
        if provider.admin_only and await require_admin(f"list {provider.name} tasks"):
            continue
        try:
            tasks.extend(await provider.list_all())
        except Exception:
            logger.warning("Could not list %s tasks", provider.name, exc_info=True)
    return tasks
