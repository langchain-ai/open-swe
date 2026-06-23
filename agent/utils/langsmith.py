"""LangSmith trace URL utilities."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from langsmith import Client as LangSmithClient
from langsmith.utils import LangSmithNotFoundError

from .tracing import AGENT_TRACING_PROJECT

logger = logging.getLogger(__name__)

_PROJECT_ID_CACHE: dict[str, str] = {}


def _build_prod_langsmith_client() -> LangSmithClient | None:
    """Build a LangSmith client scoped to the prod tenant for project lookups."""
    api_key = (
        os.environ.get("LANGSMITH_API_KEY_PROD")
        or os.environ.get("LANGSMITH_API_KEY")
        or os.environ.get("LANGCHAIN_API_KEY")
    )
    if not api_key:
        return None
    api_url = os.environ.get("LANGSMITH_ENDPOINT_PROD") or os.environ.get(
        "LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"
    )
    return LangSmithClient(api_key=api_key, api_url=api_url)


def _resolve_project_id_by_name(project_name: str) -> str | None:
    """Resolve a LangSmith project id from its name, caching both successful and
    failed lookups so an unconfigured/unauthorized tenant isn't re-queried per call."""
    if project_name in _PROJECT_ID_CACHE:
        return _PROJECT_ID_CACHE[project_name] or None
    client = _build_prod_langsmith_client()
    if client is None:
        return None
    try:
        project = client.read_project(project_name=project_name)
    except LangSmithNotFoundError:
        _PROJECT_ID_CACHE[project_name] = ""
        return None
    except Exception:  # noqa: BLE001
        logger.debug("Could not resolve LangSmith project id for %s", project_name)
        _PROJECT_ID_CACHE[project_name] = ""
        return None
    project_id = getattr(project, "id", None)
    resolved = str(project_id) if project_id else ""
    _PROJECT_ID_CACHE[project_name] = resolved
    return resolved or None


def _compose_langsmith_project_url(project_name: str = AGENT_TRACING_PROJECT) -> str | None:
    """Build the LangSmith project URL base, or None when tracing isn't configured
    for the prod tenant. Bails before any API call when the tenant id is unset."""
    tenant_id = os.environ.get("LANGSMITH_TENANT_ID_PROD")
    if not tenant_id:
        return None
    host_url = os.environ.get("LANGSMITH_URL_PROD", "https://smith.langchain.com")
    project_id = _resolve_project_id_by_name(project_name) or os.environ.get(
        "LANGSMITH_TRACING_PROJECT_ID_PROD"
    )
    if not project_id:
        return None
    return f"{host_url}/o/{tenant_id}/projects/p/{project_id}"


def get_langsmith_trace_url(
    thread_id: str, project_name: str = AGENT_TRACING_PROJECT
) -> str | None:
    """Build the LangSmith thread URL for a given thread ID, or None if tracing
    isn't configured. This is a best-effort convenience link, not an error path."""
    project_url = _compose_langsmith_project_url(project_name)
    return f"{project_url}/t/{thread_id}" if project_url else None


def _build_langsmith_feedback_clients() -> tuple[LangSmithClient, ...]:
    """Build feedback clients from current env. Re-read each call so rotated
    keys / late secret hydration are picked up."""
    clients: list[LangSmithClient] = []
    seen: set[tuple[str, str]] = set()

    api_endpoint = os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    client_configs = (
        (
            os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY"),
            api_endpoint,
        ),
        (
            os.environ.get("LANGSMITH_API_KEY_PROD"),
            os.environ.get("LANGSMITH_ENDPOINT_PROD", api_endpoint),
        ),
    )

    for api_key, api_url in client_configs:
        if not api_key or not api_url:
            continue
        identity = (api_key, api_url)
        if identity in seen:
            continue
        clients.append(LangSmithClient(api_key=api_key, api_url=api_url))
        seen.add(identity)

    return tuple(clients)


def _feedback_id(run_id: str, key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"langsmith-feedback:{run_id}:{key}")


def create_langsmith_feedback(
    run_id: str,
    key: str,
    *,
    score: float,
    comment: str | None = None,
    source_info: dict[str, Any] | None = None,
) -> bool:
    """Create or update deterministic feedback on all configured LangSmith clients."""
    clients = _build_langsmith_feedback_clients()
    if not clients:
        logger.warning("No LangSmith API key configured, skipping feedback")
        return False

    feedback_id = _feedback_id(run_id, key)
    any_success = False
    for client in clients:
        try:
            client.create_feedback(
                run_id=run_id,
                key=key,
                score=score,
                comment=comment,
                source_info=source_info,
                feedback_source_type="api",
                feedback_id=feedback_id,
            )
            any_success = True
        except Exception:
            try:
                client.update_feedback(feedback_id, score=score, comment=comment)
                any_success = True
            except Exception:
                logger.exception("Failed to create or update LangSmith feedback for run %s", run_id)
    return any_success


def delete_langsmith_feedback(run_id: str, key: str) -> bool:
    """Delete deterministic feedback from all configured LangSmith clients."""
    clients = _build_langsmith_feedback_clients()
    if not clients:
        logger.warning("No LangSmith API key configured, skipping feedback deletion")
        return False

    feedback_id = _feedback_id(run_id, key)
    any_success = False
    for client in clients:
        try:
            client.delete_feedback(feedback_id)
            any_success = True
        except LangSmithNotFoundError:
            any_success = True
        except Exception:
            logger.exception("Failed to delete LangSmith feedback for run %s", run_id)
    return any_success
