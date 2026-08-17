"""Tool for creating an authenticated sandbox file download URL."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.config import get_config

from ..utils.sandbox_downloads import create_sandbox_download_link, inspect_sandbox_file
from ..utils.sandbox_state import get_sandbox_backend

logger = logging.getLogger(__name__)


async def create_sandbox_file_download(file_path: str) -> dict[str, Any]:
    """Create a 24-hour download URL for an absolute file path in this sandbox."""
    try:
        config = get_config()
    except Exception:
        config = {}
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not isinstance(thread_id, str) or not thread_id:
        return {"success": False, "error": "no thread_id in run config"}

    try:
        backend = await get_sandbox_backend(thread_id)
        info = await inspect_sandbox_file(backend, file_path)
        link = create_sandbox_download_link(thread_id, info.path)
    except Exception as exc:
        logger.info("Could not create sandbox download for thread %s: %s", thread_id, exc)
        return {"success": False, "error": str(exc)}

    return {
        "success": True,
        "url": link.url,
        "file_path": info.path,
        "filename": info.filename,
        "size_bytes": info.size,
        "expires_at": link.expires_at.isoformat(),
    }
