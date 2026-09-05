"""LangSmith trace URL utilities."""

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langsmith import AsyncClient as AsyncLangSmithClient
from langsmith import Client as LangSmithClient
from langsmith.utils import LangSmithNotFoundError, get_host_url

from agent.config import ENV
from agent.utils.tracing import tracing_project

logger = logging.getLogger(__name__)

_PROJECT_ID_CACHE: dict[str, str] = {}
_TENANT_ID_CACHE: str | None = None
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


def langsmith_host_url() -> str:
    """Web host for trace links, derived from the API endpoint.

    ``LANGSMITH_URL_PROD`` is a deprecated explicit override.
    """
    explicit = ENV.LANGSMITH_URL_PROD.optional()
    if explicit:
        return explicit.rstrip("/")
    return str(get_host_url(None, ENV.LANGSMITH_ENDPOINT.get())).rstrip("/")


def _build_langsmith_client() -> AsyncLangSmithClient | None:
    """Build the LangSmith client used for project lookups, or None without a key."""
    api_key = ENV.LANGSMITH_API_KEY.optional()
    if not api_key:
        return None
    return async_langsmith_client(api_key, ENV.LANGSMITH_ENDPOINT.get())


def _remember_tenant_id(value: Any) -> None:
    global _TENANT_ID_CACHE
    if value and _TENANT_ID_CACHE is None:
        _TENANT_ID_CACHE = str(value)


def _discover_tenant_id() -> str | None:
    """Any project in the workspace carries the tenant id; read the first one."""
    api_key = ENV.LANGSMITH_API_KEY.optional()
    if not api_key:
        return None
    client = sync_langsmith_client(api_key, ENV.LANGSMITH_ENDPOINT.get())
    for project in client.list_projects(limit=1):
        tenant_id = getattr(project, "tenant_id", None)
        if tenant_id:
            return str(tenant_id)
    return None


async def resolve_tenant_id() -> str | None:
    """Discovered once and cached; ``LANGSMITH_TENANT_ID`` is an explicit override."""
    explicit = ENV.LANGSMITH_TENANT_ID.optional()
    if explicit:
        return explicit
    if _TENANT_ID_CACHE:
        return _TENANT_ID_CACHE
    try:
        discovered = await asyncio.to_thread(_discover_tenant_id)
    except Exception:  # noqa: BLE001
        logger.debug("Could not discover the LangSmith tenant id", exc_info=True)
        return None
    _remember_tenant_id(discovered)
    return _TENANT_ID_CACHE


async def _resolve_project_id_by_name(project_name: str) -> str | None:
    """Resolve a LangSmith project id from its name, caching definitive results."""
    if project_name in _PROJECT_ID_CACHE:
        return _PROJECT_ID_CACHE[project_name] or None
    client = _build_langsmith_client()
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
    _remember_tenant_id(getattr(project, "tenant_id", None))
    return resolved or None


async def _compose_langsmith_project_url(project_name: str | None = None) -> str | None:
    """URL base of a LangSmith project, or None when tracing isn't configured.

    Defaults to the project this deployment traces into; the review trace
    context passes the team-configured project it searches for the reviewed
    change's own traces.
    """
    tenant_id = await resolve_tenant_id()
    if not tenant_id:
        return None
    project_id = await _resolve_project_id_by_name(project_name or tracing_project())
    if not project_id:
        return None
    return f"{langsmith_host_url()}/o/{tenant_id}/projects/p/{project_id}"


async def get_langsmith_trace_url(thread_id: str, project_name: str | None = None) -> str | None:
    """Build the LangSmith thread URL for a given thread ID, or None if tracing
    isn't configured. This is a best-effort convenience link, not an error path."""
    project_url = await _compose_langsmith_project_url(project_name)
    return f"{project_url}/t/{thread_id}" if project_url else None


def _langsmith_value(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _parse_langsmith_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _langsmith_metadata_filter(key: str, value: str) -> str:
    escaped_key = key.replace("\\", "\\\\").replace('"', '\\"')
    escaped_value = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'and(eq(metadata_key, "{escaped_key}"), eq(metadata_value, "{escaped_value}"))'


async def get_langsmith_thread_cost(
    thread_id: str,
    prepare_run_id: str,
    *,
    run_only: bool = False,
) -> LangSmithThreadCost | None:
    """Return a fresh thread or run cost correlated to a completed agent run."""
    client = _build_langsmith_client()
    if client is None:
        raise LangSmithCostUnavailable("LangSmith credentials are not configured")
    project_id = await _resolve_project_id_by_name(tracing_project())
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
            if (parsed := _parse_langsmith_time(_langsmith_value(run, "end_time"))) is not None
        ]
        if not target_times:
            return None
        stats_kwargs: dict[str, Any] = {
            "session_id": project_id,
            "selects": ["TOTAL_COST", "LAST_END_TIME"],
        }
        if run_only:
            stats_kwargs["filter"] = _langsmith_metadata_filter("prepare_run_id", prepare_run_id)
        stats = await client.threads.stats(thread_id, **stats_kwargs)
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
    except TypeError, ValueError:
        return None
    last_end_time = _parse_langsmith_time(_langsmith_value(stats, "last_end_time"))
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
    """Resolve feedback client configs from current env. Re-read each call so
    rotated keys / late secret hydration are picked up."""
    configs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    client_configs = ((ENV.LANGSMITH_API_KEY.optional(), ENV.LANGSMITH_ENDPOINT.get()),)

    for api_key, api_url in client_configs:
        if not api_key or not api_url:
            continue
        identity = (api_key, api_url)
        if identity in seen:
            continue
        configs.append(identity)
        seen.add(identity)

    return tuple(configs)


def _feedback_id(run_id: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"langsmith-feedback:{run_id}:{key}")


async def create_langsmith_thread_feedback(
    thread_id: str,
    key: str,
    *,
    score: float,
    comment: str | None = None,
    source_info: dict[str, Any] | None = None,
) -> bool:
    client = _build_langsmith_client()
    if client is None:
        logger.warning("No LangSmith API key configured, skipping thread feedback")
        return False
    project_id = await _resolve_project_id_by_name(tracing_project())
    if not project_id:
        logger.warning("LangSmith tracing project is unavailable, skipping thread feedback")
        return False
    feedback_id = _feedback_id(thread_id, key)
    payload = {
        "id": str(feedback_id),
        "key": key,
        "score": score,
        "comment": comment,
        "session_id": project_id,
        "feedback_thread_id": thread_id,
        "feedback_source": {"type": "api", "metadata": source_info or {}},
    }
    try:
        await client._arequest_with_retries("POST", "/feedback", json=payload)
        return True
    except Exception:  # noqa: BLE001
        pass
    try:
        await client._arequest_with_retries(
            "PATCH",
            f"/feedback/{feedback_id}",
            json={"score": score, "comment": comment},
        )
        return True
    except Exception:
        logger.exception("Failed to create or update LangSmith thread feedback for %s", thread_id)
        return False


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
