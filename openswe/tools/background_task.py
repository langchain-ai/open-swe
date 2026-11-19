"""Inspect or stop sandbox commands and authorized Robin-wide image builds."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal, NamedTuple

from openswe.execution_images import EXECUTION_IMAGES
from openswe.tools.access import Policy, resolve_access

_PRIVATE_IMAGE_TASKS = Policy(trusted="admin_surface", actor="admin")

logger = logging.getLogger(__name__)


class _Provider(NamedTuple):
    name: str
    owns: Callable[[str], bool]
    status: Callable[[str], Awaitable[dict[str, Any]]]
    stop: Callable[[str], Awaitable[dict[str, Any]]]
    list_all: Callable[[], Awaitable[list[dict[str, Any]]]]
    # Image builds are Robin-wide: a `stop` cancels a rebuild everyone depends
    # on, and a `bash -x` trace expands arguments. Commands are the calling
    # thread's own, in its own sandbox, so they need no such gate.
    admin_only: bool


def _providers() -> tuple[_Provider, ...]:
    """Refresh first: the command provider claims every id it does not recognise."""
    from openswe.tools.background_execute import (
        owns_task,
        task_list,
        task_status,
        task_stop,
    )

    return (
        _Provider(
            "execution image build",
            EXECUTION_IMAGES.owns_task,
            EXECUTION_IMAGES.task_status,
            EXECUTION_IMAGES.task_stop,
            EXECUTION_IMAGES.task_list,
            admin_only=True,
        ),
        _Provider(
            "sandbox command", owns_task, task_status, task_stop, task_list, admin_only=False
        ),
    )


async def background_task(
    action: Literal["status", "list", "stop"], task_id: str | None = None
) -> dict[str, Any]:
    """Implement the `background_task` tool."""
    if action in {"status", "stop"} and not task_id:
        return {"success": False, "error": f"task_id is required for {action}"}
    try:
        if action == "list":
            return {"success": True, "tasks": await _list_all()}
        assert task_id is not None
        provider = next(p for p in _providers() if p.owns(task_id))
        if provider.admin_only and (await resolve_access()).mode(_PRIVATE_IMAGE_TASKS) is None:
            return {
                "success": False,
                "error": f"Only Robin admins in a private admin conversation can access {provider.name} tasks.",
            }
        result = await (provider.status if action == "status" else provider.stop)(task_id)
        return {"success": True, **result}
    except Exception as exc:
        logger.warning("background_task %s failed", action, exc_info=True)
        return {"success": False, "error": str(exc)}


async def _list_all() -> list[dict[str, Any]]:
    """List authorized tasks without one unavailable provider hiding the others."""
    tasks: list[dict[str, Any]] = []
    for provider in _providers():
        if provider.admin_only and (await resolve_access()).mode(_PRIVATE_IMAGE_TASKS) is None:
            continue
        try:
            tasks.extend(await provider.list_all())
        except Exception:
            logger.warning("Could not list %s tasks", provider.name, exc_info=True)
    return tasks
