"""LangSmith trace URL utilities."""

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from langsmith import AsyncClient as AsyncLangSmithClient
from langsmith import Client as LangSmithClient
from langsmith.utils import LangSmithNotFoundError

from ..config import (
    LangSmithPurpose,
    langsmith_app_url,
    langsmith_credentials,
    langsmith_tenant_id,
    langsmith_tracing_project_id,
)
from .timestamps import parse_expiry
from .tracing import AGENT_TRACING_PROJECT

logger = logging.getLogger(__name__)

_PROJECT_ID_CACHE: dict[str, str] = {}
_ASYNC_CLIENTS: dict[tuple[str, str], AsyncLangSmithClient] = {}
_SYNC_CLIENTS: dict[tuple[str, str], LangSmithClient] = {}


@dataclass(frozen=True)
class LangSmithThreadCost:
    total_cost: float
    last_end_time: datetime
    target_end_time: datetime


class LangSmithCostUnavailable(RuntimeError):
    pass


def async_langsmith_client(api_key: str, api_url: str) -> AsyncLangSmithClient:
    """Return a pooled ``AsyncClient`` for these credentials.

    Each client owns an ``httpx`` connection pool bound to the event loop that
    built it, so this assumes one loop per process. Keyed on the credentials so
    a test that repoints the env gets a fresh client instead of a stale pool.
    """
    key = (api_key, api_url)
    client = _ASYNC_CLIENTS.get(key)
    if client is None:
        client = _ASYNC_CLIENTS[key] = AsyncLangSmithClient(api_key=api_key, api_url=api_url)
    return client


def sync_langsmith_client(api_key: str, api_url: str) -> LangSmithClient:
    """Return a pooled sync ``Client``, for the few endpoints AsyncClient lacks."""
    key = (api_key, api_url)
    client = _SYNC_CLIENTS.get(key)
    if client is None:
        client = _SYNC_CLIENTS[key] = LangSmithClient(api_key=api_key, api_url=api_url)
    return client


def _build_prod_langsmith_client() -> AsyncLangSmithClient | None:
    """Build a LangSmith client scoped to the prod tenant for project lookups."""
    credentials = langsmith_credentials("prod")
    return async_langsmith_client(*credentials) if credentials else None


async def _resolve_project_id_by_name(project_name: str) -> str | None:
    """Resolve a LangSmith project id from its name, caching definitive results."""
    if project_name in _PROJECT_ID_CACHE:
        return _PROJECT_ID_CACHE[project_name] or None
    client = _build_prod_langsmith_client()
    if client is None:
        return None
    try:
        project = await client.read_project(project_name=project_name)
    except LangSmithNotFoundError:
        _PROJECT_ID_CACHE[project_name] = ""
        return None
    except Exception:  # noqa: BLE001
        logger.debug("Could not resolve LangSmith project id for %s", project_name, exc_info=True)
        return None
    project_id = getattr(project, "id", None)
    resolved = str(project_id) if project_id else ""
    _PROJECT_ID_CACHE[project_name] = resolved
    return resolved or None


def _project_name_is_id(project_name: str) -> bool:
    """A caller may pass a project id where a name is expected; both resolve here."""
    try:
        uuid.UUID(project_name)
    except (TypeError, ValueError):
        return False
    return True


async def _resolve_project_id(project_name: str) -> str | None:
    return (
        await _resolve_project_id_by_name(project_name)
        or langsmith_tracing_project_id()
        or (project_name if _project_name_is_id(project_name) else None)
    )


async def _compose_langsmith_project_url(project_name: str = AGENT_TRACING_PROJECT) -> str | None:
    """Build the LangSmith project URL base, or None when tracing isn't configured
    for the prod tenant. Bails before any API call when the tenant id is unset."""
    tenant_id = langsmith_tenant_id()
    if not tenant_id:
        return None
    project_id = await _resolve_project_id(project_name)
    if not project_id:
        return None
    return f"{langsmith_app_url()}/o/{tenant_id}/projects/p/{project_id}"


async def get_langsmith_trace_url(
    thread_id: str, project_name: str = AGENT_TRACING_PROJECT
) -> str | None:
    """Build the LangSmith thread URL for a given thread ID, or None if tracing
    isn't configured. This is a best-effort convenience link, not an error path."""
    project_url = await _compose_langsmith_project_url(project_name)
    return f"{project_url}/t/{thread_id}" if project_url else None


def _langsmith_value(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _langsmith_metadata_filter(key: str, value: str) -> str:
    escaped_key = key.replace("\\", "\\\\").replace('"', '\\"')
    escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'and(eq(metadata_key, "{escaped_key}"), eq(metadata_value, "{escaped_value}"))'


async def get_langsmith_thread_cost(
    thread_id: str,
    prepare_run_id: str,
    project_name: str = AGENT_TRACING_PROJECT,
) -> LangSmithThreadCost | None:
    """Return a cumulative thread cost correlated to a completed agent run."""
    client = _build_prod_langsmith_client()
    if client is None:
        raise LangSmithCostUnavailable("LangSmith credentials are not configured")
    project_id = await _resolve_project_id(project_name)
    if not project_id:
        raise LangSmithCostUnavailable("LangSmith tracing project is unavailable")
    try:
        roots = client.list_runs(
            project_id=project_id,
            is_root=True,
            filter=_langsmith_metadata_filter("prepare_run_id", prepare_run_id),
            select=["end_time"],
            limit=20,
        )
        target_times = [
            parsed
            async for run in roots
            if (parsed := parse_expiry(_langsmith_value(run, "end_time"))) is not None
        ]
        if not target_times:
            return None
        stats = await client.threads.stats(
            thread_id,
            session_id=project_id,
            selects=["TOTAL_COST", "LAST_END_TIME"],
        )
    except LangSmithNotFoundError as exc:
        raise LangSmithCostUnavailable("LangSmith thread stats are unsupported") from exc
    except Exception as exc:  # noqa: BLE001
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int) and 400 <= status_code < 500 and status_code != 429:
            raise LangSmithCostUnavailable("LangSmith thread stats are unsupported") from exc
        logger.debug("Could not load LangSmith cost for thread %s", thread_id, exc_info=True)
        return None

    raw_cost = _langsmith_value(stats, "total_cost")
    if isinstance(raw_cost, bool):
        return None
    try:
        total_cost = float(raw_cost)
    except (TypeError, ValueError):
        return None
    last_end_time = parse_expiry(_langsmith_value(stats, "last_end_time"))
    target_end_time = max(target_times)
    if (
        not math.isfinite(total_cost)
        or total_cost < 0
        or last_end_time is None
        or last_end_time < target_end_time
    ):
        return None
    return LangSmithThreadCost(
        total_cost=total_cost,
        last_end_time=last_end_time,
        target_end_time=target_end_time,
    )


def _build_langsmith_feedback_clients() -> tuple[tuple[str, str], ...]:
    """Every tenant that should receive feedback, deduplicated.

    Feedback is written wherever the run may have been traced, so both the
    platform workspace and the prod tenant get a copy when they differ.
    """
    configs: list[tuple[str, str]] = []
    purposes: tuple[LangSmithPurpose, ...] = ("platform", "prod")
    for purpose in purposes:
        credentials = langsmith_credentials(purpose)
        if credentials is not None and credentials not in configs:
            configs.append(credentials)
    return tuple(configs)


def _feedback_id(run_id: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"langsmith-feedback:{run_id}:{key}")


async def _update_feedback(
    api_key: str,
    api_url: str,
    feedback_id: uuid.UUID,
    *,
    score: float,
    comment: str | None,
) -> None:
    # AsyncClient exposes no update_feedback; the sync one runs off-loop.
    client = sync_langsmith_client(api_key, api_url)
    await asyncio.to_thread(client.update_feedback, feedback_id, score=score, comment=comment)


async def create_langsmith_feedback(
    run_id: str,
    key: str,
    *,
    score: float,
    comment: str | None = None,
    source_info: dict[str, Any] | None = None,
) -> bool:
    """Create or update deterministic feedback on all configured LangSmith tenants."""
    configs = _build_langsmith_feedback_clients()
    if not configs:
        logger.warning("No LangSmith API key configured, skipping feedback")
        return False

    feedback_id = _feedback_id(run_id, key)
    any_success = False
    for api_key, api_url in configs:
        try:
            await async_langsmith_client(api_key, api_url).create_feedback(
                run_id=run_id,
                key=key,
                score=score,
                comment=comment,
                source_info=source_info,
                feedback_source_type="api",
                feedback_id=feedback_id,
            )
            any_success = True
            continue
        except Exception:  # noqa: BLE001 - feedback already exists; update in place
            pass
        try:
            await _update_feedback(api_key, api_url, feedback_id, score=score, comment=comment)
            any_success = True
        except Exception:
            logger.exception("Failed to create or update LangSmith feedback for run %s", run_id)
    return any_success


async def delete_langsmith_feedback(run_id: str, key: str) -> bool:
    """Delete deterministic feedback from all configured LangSmith tenants."""
    configs = _build_langsmith_feedback_clients()
    if not configs:
        logger.warning("No LangSmith API key configured, skipping feedback deletion")
        return False

    feedback_id = _feedback_id(run_id, key)
    any_success = False
    for api_key, api_url in configs:
        try:
            await async_langsmith_client(api_key, api_url).delete_feedback(feedback_id)
            any_success = True
        except LangSmithNotFoundError:
            any_success = True
        except Exception:
            logger.exception("Failed to delete LangSmith feedback for run %s", run_id)
    return any_success
