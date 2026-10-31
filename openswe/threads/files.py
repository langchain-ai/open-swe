"""Browsing a thread's sandbox workspace: list a directory, read a file, or index files."""

import base64
import json
import logging
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, TypeAdapter

from openswe.dashboard.oauth import DashboardSession
from openswe.sandboxes import scripts as sandbox_scripts
from openswe.threads.access import _readable_thread_metadata
from openswe.threads.diffs import _response_output, connect_sandbox
from openswe.threads.summary import _assert_thread_promptable, _metadata_repo

logger = logging.getLogger(__name__)

_WORKSPACE_PATH_TIMEOUT_SECONDS = 30


class WorkspaceEntry(BaseModel):
    path: str
    kind: Literal["file", "directory"]
    ignored: bool


class WorkspaceDirectory(BaseModel):
    kind: Literal["directory"]
    entries: list[WorkspaceEntry]


class WorkspaceFile(BaseModel):
    kind: Literal["file"]
    contents: str
    binary: bool
    truncated: bool
    size: int


WorkspacePath = Annotated[WorkspaceDirectory | WorkspaceFile, Field(discriminator="kind")]


class WorkspaceFileIndex(BaseModel):
    """Every non-ignored file in the workspace, for search."""

    paths: list[str]


_WORKSPACE_PATH_ADAPTER: TypeAdapter[WorkspaceDirectory | WorkspaceFile] = TypeAdapter(
    WorkspacePath
)


def _workspace_path_script() -> str:
    return sandbox_scripts.source("workspace_path.py")


def _workspace_path_command(root: str, mode: Literal["path", "index"], path: str) -> str:
    payload = {"root": root, "mode": mode, "path": path}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    script = _workspace_path_script().replace("__PAYLOAD__", encoded)
    return f"python3 - <<'PY'\n{script}PY"


async def _run_workspace_script(
    thread_id: str,
    session: DashboardSession,
    mode: Literal["path", "index"],
    path: str,
) -> dict[str, object]:
    from openswe.sandboxes.paths import resolve_checkout_dir, resolve_sandbox_work_dir

    metadata = await _readable_thread_metadata(thread_id, session)
    # Reading the live sandbox is shell-equivalent access, so it follows the
    # terminal's rule: admins who may view a private thread still cannot.
    _assert_thread_promptable(metadata, session)
    sandbox_id = metadata.get("sandbox_id")
    if not isinstance(sandbox_id, str) or not sandbox_id:
        raise HTTPException(404, "thread has no workspace")
    try:
        sandbox = await connect_sandbox(sandbox_id, thread_id=thread_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not connect to sandbox for files", extra={"sandbox": sandbox_id})
        raise HTTPException(503, "Could not connect to the workspace.") from exc
    work_dir = await resolve_sandbox_work_dir(sandbox)
    _, repo_name, _ = _metadata_repo(metadata)
    root = await resolve_checkout_dir(sandbox, work_dir, repo_name) if repo_name else work_dir
    result = await sandbox.aexecute(
        _workspace_path_command(root, mode, path), timeout=_WORKSPACE_PATH_TIMEOUT_SECONDS
    )
    output = _response_output(result).strip()
    try:
        payload = json.loads(output.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        logger.warning("Invalid workspace path response", extra={"thread_id": thread_id})
        raise HTTPException(502, "Could not read the workspace.") from exc
    if "error" in payload:
        raise HTTPException(404, payload["error"])
    return payload


async def get_dashboard_thread_path(
    thread_id: str,
    session: DashboardSession,
    path: str,
) -> WorkspaceDirectory | WorkspaceFile:
    """List a directory or read a file relative to the thread's repository."""
    payload = await _run_workspace_script(thread_id, session, "path", path)
    return _WORKSPACE_PATH_ADAPTER.validate_python(payload)


async def get_dashboard_thread_file_index(
    thread_id: str,
    session: DashboardSession,
) -> WorkspaceFileIndex:
    """List every non-ignored file in the thread's repository."""
    payload = await _run_workspace_script(thread_id, session, "index", "")
    return WorkspaceFileIndex.model_validate(payload)
