"""The one LangGraph cron ritual, and the scheduler graph's wire format.

Every recurring job registers the same way: search for a cron carrying this
job's ``(kind, key)`` metadata, keep the first and drop any duplicate, and only
create one when none exists. Search and create are not atomic, so duplicates
happen; healing them in one place is what keeps recovery identical across jobs.

A scheduler tick carries its whole payload in the run **input**, shaped as
``{"task": kind, "payload": {...}}``. Nothing is read from
``config.configurable``: the graph declares three channels and never grows one
per task, and a producer that forgets to mirror a field cannot exist.

Every helper takes the client to use, because the transport belongs to the
caller: code running inside the LangGraph server passes
:func:`agent.config.in_process_langgraph_client`, and the HTTP layer passes
:func:`agent.config.langgraph_client`.
"""

import logging
from collections.abc import Callable, Mapping
from typing import Any, TypedDict

from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.schema import Config, Input

logger = logging.getLogger(__name__)

SCHEDULER_ASSISTANT_ID = "scheduler"
_SEARCH_LIMIT = 10

# Crons registered before the metadata became uniformly ``{"kind", "key"}``.
# Searched next to the current shape so the ensure/delete that manages a job's
# cron also retires the predecessor it would otherwise leave firing forever.
# Drop this table once no pre-migration cron survives.
_LEGACY_METADATA: dict[str, Callable[[str], dict[str, str]]] = {
    "baby_sit": lambda key: {"kind": "baby_sit_watch", "watch_key": key},
    "background_tasks": lambda key: {"kind": "background_tasks", "thread_id": key},
    "schedule": lambda key: {"kind": "agent_schedule", "schedule_id": key},
}


class SchedulerRunInput(TypedDict):
    task: str
    payload: dict[str, Any]


def scheduler_run_input(kind: str, payload: Mapping[str, Any]) -> SchedulerRunInput:
    return {"task": kind, "payload": dict(payload)}


def _cron_id(cron: Any) -> str | None:
    value = cron.get("cron_id") if isinstance(cron, dict) else getattr(cron, "cron_id", None)
    return value if isinstance(value, str) and value else None


async def create_cron(
    client: LangGraphClient,
    assistant_id: str,
    *,
    schedule: str,
    metadata: dict[str, Any],
    input: Input | None = None,
    config: Config | None = None,
    timezone: str = "UTC",
) -> str:
    """Create one cron and return its id, refusing a response without one."""
    cron = await client.crons.create(
        assistant_id,
        schedule=schedule,
        input=input,
        config=config,
        metadata=metadata,
        timezone=timezone,
    )
    cron_id = _cron_id(cron)
    if cron_id is None:
        raise RuntimeError(f"{assistant_id} cron creation did not return a cron_id")
    return cron_id


async def delete_cron(client: LangGraphClient, cron_id: str | None) -> bool:
    """Delete one cron. ``False`` means the platform refused; it may still fire."""
    if not cron_id:
        return True
    try:
        await client.crons.delete(cron_id)
    except Exception:
        logger.warning("Could not delete cron %s", cron_id, exc_info=True)
        return False
    return True


async def _search_cron_ids(client: LangGraphClient, metadata: dict[str, str]) -> list[str]:
    crons = await client.crons.search(
        assistant_id=SCHEDULER_ASSISTANT_ID,
        metadata=metadata,
        limit=_SEARCH_LIMIT,
    )
    return [cron_id for cron in crons or [] if (cron_id := _cron_id(cron)) is not None]


async def _legacy_cron_ids(client: LangGraphClient, kind: str, key: str) -> list[str]:
    legacy = _LEGACY_METADATA.get(kind)
    return await _search_cron_ids(client, legacy(key)) if legacy is not None else []


async def ensure_scheduler_cron(
    client: LangGraphClient, *, kind: str, key: str, schedule: str, payload: Mapping[str, Any]
) -> str:
    """Register the ``(kind, key)`` scheduler cron once, healing duplicates."""
    for outdated in await _legacy_cron_ids(client, kind, key):
        await delete_cron(client, outdated)
    existing = await _search_cron_ids(client, {"kind": kind, "key": key})
    if existing:
        for duplicate in existing[1:]:
            await delete_cron(client, duplicate)
        return existing[0]
    return await create_cron(
        client,
        SCHEDULER_ASSISTANT_ID,
        schedule=schedule,
        metadata={"kind": kind, "key": key},
        input=scheduler_run_input(kind, payload),
    )


async def delete_scheduler_crons(
    client: LangGraphClient, *, kind: str, key: str, cron_id: str | None = None
) -> bool:
    """Delete every scheduler cron registered for ``(kind, key)``.

    ``cron_id`` is a caller-recorded handle on the same job, deleted as well
    when no search returned it.
    """
    cron_ids = await _search_cron_ids(client, {"kind": kind, "key": key})
    cron_ids.extend(await _legacy_cron_ids(client, kind, key))
    if cron_id and cron_id not in cron_ids:
        cron_ids.append(cron_id)
    deleted = True
    for found in cron_ids:
        deleted = await delete_cron(client, found) and deleted
    return deleted
