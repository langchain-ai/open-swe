"""LangSmith trace URL utilities."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _compose_langsmith_url_base() -> str:
    """Build the LangSmith URL base from environment variables."""
    host_url = os.environ.get("LANGSMITH_URL_PROD", "https://smith.langchain.com")
    tenant_id = os.environ.get("LANGSMITH_TENANT_ID_PROD")
    project_id = os.environ.get("LANGSMITH_TRACING_PROJECT_ID_PROD")
    if not tenant_id or not project_id:
        raise ValueError(
            "LANGSMITH_TENANT_ID_PROD and LANGSMITH_TRACING_PROJECT_ID_PROD must be set"
        )
    return f"{host_url}/o/{tenant_id}/projects/p/{project_id}/r"


def get_langsmith_trace_url(run_id: str) -> str | None:
    """Build the LangSmith trace URL for a given run ID."""
    try:
        url_base = _compose_langsmith_url_base()
        return f"{url_base}/{run_id}?poll=true"
    except Exception:  # noqa: BLE001
        logger.warning("Failed to build LangSmith trace URL for run %s", run_id, exc_info=True)
        return None
