"""Tool: ``publish_artifact``. Publish a sandbox HTML artifact for review or sharing."""

import logging
import re
from collections.abc import Mapping
from typing import Any

from openswe.run_config import RunConfig
from openswe.sandboxes.state import get_sandbox_backend
from openswe.threads.plan_store import (
    PLAN_FILE_DIRECTORY,
    PLAN_STATUS_SHARED,
    save_plan_content,
)
from openswe.utils.html_artifact import DEFAULT_TITLE, wrap_html_artifact

logger = logging.getLogger(__name__)

_MAX_ARTIFACT_LINES = 20_000


async def publish_artifact(
    artifact_file_path: str,
) -> dict[str, Any]:
    """Implement the `publish_artifact` tool."""
    if not isinstance(artifact_file_path, str):
        return {"success": False, "error": "artifact_file_path must be a string"}
    path = artifact_file_path.strip()
    if not path:
        return {"success": False, "error": "artifact_file_path cannot be empty"}
    if not _is_html_path(path):
        return {
            "success": False,
            "error": f"artifact_file_path must point to an HTML file in {PLAN_FILE_DIRECTORY}",
        }

    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not thread_id:
        return {"success": False, "error": "no thread_id in run config"}

    try:
        content = (await _read_artifact_file(str(thread_id), path)).strip()
        if not content:
            return {"success": False, "error": "artifact file cannot be empty"}
        document = wrap_html_artifact(content, title=_title_from_path(path))
        await save_plan_content(
            str(thread_id), html=document, status=PLAN_STATUS_SHARED, plan_file_path=path
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("publish_artifact failed for thread %s", thread_id)
        return {"success": False, "error": f"failed to publish artifact: {exc}"}
    return {"success": True, "path": path}


async def _read_artifact_file(thread_id: str, path: str) -> str:
    backend = await get_sandbox_backend(thread_id)
    result = await backend.aread(path, offset=0, limit=_MAX_ARTIFACT_LINES)
    error = _value(result, "error")
    if error:
        raise ValueError(error)
    file_data = _value(result, "file_data")
    if file_data is None:
        raise ValueError("artifact file could not be read")
    encoding = _value(file_data, "encoding")
    if encoding is not None and encoding != "utf-8":
        raise ValueError("artifact file must be UTF-8 text")
    content = _value(file_data, "content")
    if not isinstance(content, str):
        raise ValueError("artifact file content was not text")
    if content.count("\n") + 1 >= _MAX_ARTIFACT_LINES:
        raise ValueError("artifact file is too large")
    return content


def _value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _is_html_path(path: str) -> bool:
    if "\x00" in path or not path.startswith(f"{PLAN_FILE_DIRECTORY}/"):
        return False
    filename = path.removeprefix(f"{PLAN_FILE_DIRECTORY}/")
    return bool(filename and "/" not in filename and filename.lower().endswith(".html"))


def _title_from_path(path: str) -> str:
    """Fallback artifact name for an artifact file that carries no title of its own."""
    stem = path.rsplit("/", 1)[-1].removesuffix(".html")
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", stem)
    words = stem.replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else DEFAULT_TITLE
