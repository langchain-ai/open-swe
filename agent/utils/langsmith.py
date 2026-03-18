"""LangSmith trace URL utilities."""

from __future__ import annotations

import asyncio
import functools
import logging
import os

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _fetch_langsmith_url_base() -> str:
    """Fetch and build the LangSmith URL base (blocking — run in a thread)."""
    from langsmith import Client

    from agent.integrations.langsmith import _get_langsmith_api_key

    client = Client(api_key=_get_langsmith_api_key())
    project_name = os.environ.get("LANGSMITH_PROJECT") or os.environ.get("LANGSMITH_PROJECT_PROD")
    project_id = str(client.read_project(project_name=project_name).id)
    tenant_id = str(client._get_tenant_id())
    return f"{client._host_url}/o/{tenant_id}/projects/p/{project_id}/r"


async def get_langsmith_trace_url(run_id: str) -> str | None:
    """Build the LangSmith trace URL for a given run ID."""
    try:
        url_base = await asyncio.to_thread(_fetch_langsmith_url_base)
        url = f"{url_base}/{run_id}?poll=true"
        return url
    except Exception:  # noqa: BLE001
        logger.warning("Failed to build LangSmith trace URL for run %s", run_id, exc_info=True)
        return None
