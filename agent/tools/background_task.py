"""One poll for every kind of background work.

Two things run in the background and both take long enough that the model has to
be able to ask "how far has it got": a command launched with
``background_execute``, and an environment refresh started by
``refresh_environment_start`` or ``save_environment``. They keep their state in
different places — a command's in the thread sandbox's own filesystem, a
refresh's on the environment record plus a live trace on its builder — so each
kind owns a provider that knows how to read it, and this tool routes by the
task id's prefix.

Both report the same fields: ``status``, ``started_at``/``finished_at``,
``output``, and where that output came from. A refresh adds ``steps``, because a
rebuild is minutes to an hour of setup script and a single pending status cannot
tell slow from wedged.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal, NamedTuple

from agent.dashboard import environment_refresh
from agent.tools.admin_gate import require_admin

logger = logging.getLogger(__name__)


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
            "environment refresh",
            environment_refresh.owns_task,
            environment_refresh.task_status,
            environment_refresh.task_stop,
            environment_refresh.task_list,
            admin_only=True,
        ),
        _Provider(
            "sandbox command", owns_task, task_status, task_stop, task_list, admin_only=False
        ),
    )


async def background_task(
    action: Literal["status", "list", "stop"], task_id: str | None = None
) -> dict[str, Any]:
    """Inspect or stop background work: sandbox commands and environment refreshes.

    `status` and `stop` require `task_id`; `list` does not. A refresh's status carries
    `steps` — which stage it reached — and, while a script is running, a tail of that
    script's live `bash -x` trace read off the builder sandbox; environment refreshes
    are visible only to workspace admins. Status reads are for explicit user requests,
    for following a long rebuild, or when completion needs inspection — not for
    polling loops.
    """
    if action in {"status", "stop"} and not task_id:
        return {"success": False, "error": f"task_id is required for {action}"}
    try:
        if action == "list":
            return {"success": True, "tasks": await _list_all()}
        assert task_id is not None
        provider = next(p for p in _providers() if p.owns(task_id))
        if provider.admin_only and (denied := require_admin(f"read {provider.name} tasks")):
            return {"success": False, "error": denied}
        result = await (provider.status if action == "status" else provider.stop)(task_id)
        return {"success": True, **result}
    except Exception as exc:
        logger.warning("background_task %s failed", action, exc_info=True)
        return {"success": False, "error": str(exc)}


async def _list_all() -> list[dict[str, Any]]:
    """Every task from every provider.

    One provider failing must not blank the listing: a thread with no sandbox
    bound cannot list commands, but its environment refreshes are still visible.
    """
    tasks: list[dict[str, Any]] = []
    for provider in _providers():
        if provider.admin_only and require_admin(f"list {provider.name} tasks"):
            continue
        try:
            tasks.extend(await provider.list_all())
        except Exception:
            logger.warning("Could not list %s tasks", provider.name, exc_info=True)
    return tasks
