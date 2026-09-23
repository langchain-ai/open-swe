"""Browsing a thread's sandbox workspace: list a directory or read a file."""

import base64
import json
import logging
import posixpath
from functools import cache
from importlib import resources
from typing import Any

from fastapi import HTTPException

from agent.threads.access import _readable_thread_metadata
from agent.threads.diffs import _response_output, create_sandbox
from agent.threads.summary import _metadata_repo

logger = logging.getLogger(__name__)

_WORKSPACE_PATH_TIMEOUT_SECONDS = 30


@cache
def _workspace_path_script() -> str:
    return (
        resources.files("agent.resources").joinpath("workspace_path.py").read_text(encoding="utf-8")
    )


def _workspace_path_command(root: str, fallback: str, path: str) -> str:
    payload = {"root": root, "fallback": fallback, "path": path}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    script = _workspace_path_script().replace("__PAYLOAD__", encoded)
    return f"python3 - <<'PY'\n{script}PY"


async def get_dashboard_thread_path(
    thread_id: str, login: str, path: str, *, email: str | None = None
) -> dict[str, Any]:
    """List a directory or read a file relative to the thread's repository."""
    from agent.sandboxes.paths import resolve_sandbox_work_dir

    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    sandbox_id = metadata.get("sandbox_id")
    if not isinstance(sandbox_id, str) or not sandbox_id:
        raise HTTPException(404, "thread has no workspace")
    try:
        sandbox = await create_sandbox(sandbox_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not connect to sandbox for files", extra={"sandbox": sandbox_id})
        raise HTTPException(503, "Could not connect to the workspace.") from exc
    work_dir = await resolve_sandbox_work_dir(sandbox)
    _, repo_name, _ = _metadata_repo(metadata)
    root = posixpath.join(work_dir, repo_name) if repo_name else work_dir
    result = await sandbox.aexecute(
        _workspace_path_command(root, work_dir, path), timeout=_WORKSPACE_PATH_TIMEOUT_SECONDS
    )
    output = _response_output(result).strip()
    try:
        payload = json.loads(output.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        logger.warning("Invalid workspace path response", extra={"thread": thread_id})
        raise HTTPException(502, "Could not read the workspace.") from exc
    if "error" in payload:
        raise HTTPException(404, payload["error"])
    return payload
