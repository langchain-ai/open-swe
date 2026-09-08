"""Tool: ``save_plan``. Publish a sandbox HTML artifact for review or sharing."""

import logging
import re
from collections.abc import Mapping
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from agent.dashboard.plan_store import (
    PLAN_FILE_DIRECTORY,
    PLAN_STATUS_READY,
    PLAN_STATUS_SHARED,
    save_plan_content,
)
from agent.run_config import RunConfig
from agent.sandboxes.state import get_sandbox_backend
from agent.utils.html_artifact import DEFAULT_TITLE, wrap_html_artifact

logger = logging.getLogger(__name__)

_MAX_PLAN_LINES = 20_000


async def _save_plan(
    plan_file_path: str,
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not isinstance(plan_file_path, str):
        return {"success": False, "error": "plan_file_path must be a string"}, None
    path = plan_file_path.strip()
    if not path:
        return {"success": False, "error": "plan_file_path cannot be empty"}, None
    if not _is_html_path(path):
        return (
            {
                "success": False,
                "error": f"plan_file_path must point to an HTML file in {PLAN_FILE_DIRECTORY}",
            },
            None,
        )

    cfg = RunConfig.from_runtime()
    thread_id = cfg.thread_id
    if not thread_id:
        return {"success": False, "error": "no thread_id in run config"}, None

    try:
        content = (await _read_plan_file(str(thread_id), path)).strip()
        if not content:
            return {"success": False, "error": "plan file cannot be empty"}, None
        title = _title_from_path(path)
        document = wrap_html_artifact(content, title=title)
        await _save(str(thread_id), document, path, plan_mode=_active_plan_mode(state, cfg))
    except Exception as exc:  # noqa: BLE001
        logger.exception("save_plan failed for thread %s", thread_id)
        return {"success": False, "error": f"failed to save plan: {exc}"}, None
    return (
        {"success": True, "path": path},
        {"type": "plan", "html": document, "title": title, "path": path},
    )


save_plan = tool(
    "save_plan",
    description="""Publish a self-contained HTML plan artifact from the sandbox.

Use this in plan mode once the artifact is ready. Outside plan mode, use it
for a long visual response. Write one `.html` file directly under
`/workspace/plans/` and pass that path here. Read the `html-artifacts` skill
first. The dashboard renders every publication inline at this tool call and
keeps the latest publication available on the full plan page.""",
    response_format="content_and_artifact",
)(_save_plan)


async def _save(thread_id: str, content: str, path: str, *, plan_mode: bool) -> None:
    await save_plan_content(
        thread_id,
        html=content,
        status=PLAN_STATUS_READY if plan_mode else PLAN_STATUS_SHARED,
        plan_file_path=path,
        plan_mode=plan_mode or None,
    )


def _active_plan_mode(state: dict[str, Any] | None, cfg: RunConfig) -> bool:
    if isinstance(state, dict) and state.get("plan_mode") is True:
        return True
    return cfg.plan_mode is True


async def _read_plan_file(thread_id: str, path: str) -> str:
    backend = await get_sandbox_backend(thread_id)
    result = await backend.aread(path, offset=0, limit=_MAX_PLAN_LINES)
    error = _value(result, "error")
    if error:
        raise ValueError(error)
    file_data = _value(result, "file_data")
    if file_data is None:
        raise ValueError("plan file could not be read")
    encoding = _value(file_data, "encoding")
    if encoding is not None and encoding != "utf-8":
        raise ValueError("plan file must be UTF-8 text")
    content = _value(file_data, "content")
    if not isinstance(content, str):
        raise ValueError("plan file content was not text")
    if content.count("\n") + 1 >= _MAX_PLAN_LINES:
        raise ValueError("plan file is too large")
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
    """Fallback artifact name for a plan file that carries no title of its own."""
    stem = path.rsplit("/", 1)[-1].removesuffix(".html")
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", stem)
    words = stem.replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else DEFAULT_TITLE
