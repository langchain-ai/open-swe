"""LangSmith trace URL utilities and scoped-project client access.

The scoped client and the trace tools built on top of it are deliberately
pinned to a single project ID from the environment (`LANGSMITH_TRACING_PROJECT_ID_PROD`).
Callers cannot override the project — this is the security boundary that
keeps agent-exposed trace tools from reaching across projects in the
workspace, analogous to how smith-issues-agent scopes every CLI call to
the per-run `session_id` from its configurable.
"""

from __future__ import annotations

import logging
import os

from langsmith import Client

logger = logging.getLogger(__name__)


def _compose_langsmith_project_url() -> str:
    """Build the LangSmith project URL base from environment variables."""
    host_url = os.environ.get("LANGSMITH_URL_PROD", "https://smith.langchain.com")
    tenant_id = os.environ.get("LANGSMITH_TENANT_ID_PROD")
    project_id = os.environ.get("LANGSMITH_TRACING_PROJECT_ID_PROD")
    if not tenant_id or not project_id:
        raise ValueError(
            "LANGSMITH_TENANT_ID_PROD and LANGSMITH_TRACING_PROJECT_ID_PROD must be set"
        )
    return f"{host_url}/o/{tenant_id}/projects/p/{project_id}"


def get_langsmith_trace_url(thread_id: str) -> str | None:
    """Build the LangSmith thread URL for a given thread ID."""
    try:
        project_url = _compose_langsmith_project_url()
        return f"{project_url}/t/{thread_id}"
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to build LangSmith trace URL for thread %s", thread_id, exc_info=True
        )
        return None


def get_scoped_langsmith_project_id() -> str:
    """Return the single project ID this deployment is allowed to read.

    Sourced from `LANGSMITH_TRACING_PROJECT_ID_PROD` — the same env var used
    to build trace URLs in `_compose_langsmith_project_url`. Callers must
    never accept a project id from tool arguments; this function is the
    only source of truth.
    """
    project_id = os.environ.get("LANGSMITH_TRACING_PROJECT_ID_PROD")
    if not project_id:
        raise RuntimeError(
            "LANGSMITH_TRACING_PROJECT_ID_PROD must be set to use the LangSmith "
            "trace tools — they are scoped to this single project."
        )
    return project_id


def _get_langsmith_api_key() -> str | None:
    """Resolve the LangSmith API key, preferring prod-reserved name.

    LangGraph Cloud reserves `LANGSMITH_API_KEY` for its own tracing, so
    deployments also set `LANGSMITH_API_KEY_PROD` for application use.
    """
    return os.environ.get("LANGSMITH_API_KEY_PROD") or os.environ.get("LANGSMITH_API_KEY")


def get_scoped_langsmith_client() -> Client:
    """Return a LangSmith client for read-only trace queries.

    The caller is responsible for pinning every request to
    `get_scoped_langsmith_project_id()`. This function only validates that
    credentials exist; it does not enforce scope on its own.
    """
    api_key = _get_langsmith_api_key()
    if not api_key:
        raise RuntimeError(
            "No LangSmith API key found. Set LANGSMITH_API_KEY_PROD (or "
            "LANGSMITH_API_KEY) to enable the LangSmith trace tools."
        )
    endpoint = os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    return Client(api_url=endpoint, api_key=api_key)
