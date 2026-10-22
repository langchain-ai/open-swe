"""The LangSmith tracing project this deployment writes runs to."""

from __future__ import annotations

import asyncio
from time import monotonic

from managed_deepagents._log import logger

_LOOKUP_TIMEOUT_SECONDS = 2.0
_RETRY_AFTER_SECONDS = 60.0
_project_ids: dict[str, str] = {}
_next_lookup_at: dict[str, float] = {}


async def tracer_session_id() -> str | None:
    """Id of the project the tracer writes this process's runs to.

    The tracer addresses its project by name, so the id comes from that same
    name. An id recorded at deploy time goes stale when someone renames the
    project, because new traces then land in a fresh project under the old name.

    ``None`` when tracing is off or no id is known yet, which costs a runtime
    event its trace link and nothing else. A found id is cached for the
    process. At most one lookup starts per minute, so a failing lookup cannot
    hold up every event, and a caller that arrives while one is in flight
    returns at once rather than starting another.
    """
    from langsmith import utils as ls_utils  # noqa: PLC0415

    # "local" traces never reach LangSmith, so they have nothing to link.
    if ls_utils.tracing_is_enabled() is not True:
        return None
    name = ls_utils.get_tracer_project()
    if not name:
        return None
    if cached := _project_ids.get(name):
        return cached
    now = monotonic()
    if now < _next_lookup_at.get(name, 0.0):
        return None
    _next_lookup_at[name] = now + _RETRY_AFTER_SECONDS
    try:
        project_id = await asyncio.wait_for(
            asyncio.to_thread(_read_project_id, name),
            _LOOKUP_TIMEOUT_SECONDS,
        )
    except Exception as error:  # noqa: BLE001 - a missing link must not cost the event
        logger.warning("Tracing project lookup failed (%s).", logger.safe_error_label(error))
        return None
    _project_ids[name] = project_id
    return project_id


def _read_project_id(name: str) -> str:
    from langsmith import Client  # noqa: PLC0415

    return str(Client().read_project(project_name=name).id)
