"""LangSmith trace URL utilities."""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_langsmith_url_base: str | None = None


def _fetch_langsmith_url_base() -> str:
    """Fetch and build the LangSmith URL base (blocking — run in a thread)."""
    from langsmith import Client

    client = Client()
    project_name = os.environ.get("LANGSMITH_PROJECT")
    project_id = str(client.read_project(project_name=project_name).id)
    tenant_id = str(client._get_tenant_id())
    return f"{client._host_url}/o/{tenant_id}/projects/p/{project_id}/r"


async def get_langsmith_trace_url(run_id: str) -> str | None:
    """Build the LangSmith trace URL for a given run ID."""
    global _langsmith_url_base
    try:
        if _langsmith_url_base is None:
            _langsmith_url_base = await asyncio.to_thread(_fetch_langsmith_url_base)
        url = f"{_langsmith_url_base}/{run_id}?poll=true"
        logger.info("LangSmith trace URL for run %s: %s", run_id, url)
        return url
    except Exception:  # noqa: BLE001
        logger.warning("Failed to build LangSmith trace URL for run %s", run_id, exc_info=True)
        return None
