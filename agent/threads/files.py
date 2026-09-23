"""Browsing a thread's sandbox workspace: list a directory, read a file, or index files."""

import base64
import json
import logging
import posixpath
from functools import cache
from importlib import resources
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from agent.threads.access import _readable_thread_metadata
from agent.threads.diffs import _response_output, create_sandbox
from agent.threads.summary import _assert_thread_promptable, _metadata_repo

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
    truncated: bool


class _WorkspaceError(BaseModel):
    error: str


_WORKSPACE_PATH_ADAPTER: TypeAdapter[_WorkspaceError | WorkspacePath] = TypeAdapter(
    _WorkspaceError | WorkspacePath
)
_WORKSPACE_INDEX_ADAPTER: TypeAdapter[_WorkspaceError | WorkspaceFileIndex] = TypeAdapter(
    _WorkspaceError | WorkspaceFileIndex
)


@cache
def _workspace_path_script() -> str:
    return (
        resources.files("agent.resources").joinpath("workspace_path.py").read_text(encoding="utf-8")
    )


def _workspace_path_command(root: str, mode: Literal["path", "index"], path: str) -> str:
    payload = {"root": root, "mode": mode, "path": path}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    script = _workspace_path_script().replace("__PAYLOAD__", encoded)
    return f"python3 - <<'PY'\n{script}PY"


async def _run_workspace_script(
    thread_id: str,
    login: str,
    email: str | None,
    mode: Literal["path", "index"],
    path: str,
) -> str:
    from agent.sandboxes.paths import resolve_sandbox_work_dir

    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    # Reading the live sandbox is shell-equivalent access, so it follows the
    # terminal's rule: admins who may view a private thread still cannot.
    _assert_thread_promptable(metadata, login)
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
        _workspace_path_command(root, mode, path), timeout=_WORKSPACE_PATH_TIMEOUT_SECONDS
    )
    lines = _response_output(result).strip().splitlines()
    if not lines:
        logger.warning("Empty workspace path response", extra={"thread": thread_id})
        raise HTTPException(502, "Could not read the workspace.")
    return lines[-1]


def _parse_workspace_output[T](
    adapter: TypeAdapter[_WorkspaceError | T], thread_id: str, output: str
) -> T:
    try:
        payload = adapter.validate_json(output)
    except ValidationError as exc:
        logger.warning("Invalid workspace path response", extra={"thread": thread_id})
        raise HTTPException(502, "Could not read the workspace.") from exc
    if isinstance(payload, _WorkspaceError):
        raise HTTPException(404, payload.error)
    return payload


async def get_dashboard_thread_path(
    thread_id: str, login: str, path: str, *, email: str | None = None
) -> WorkspaceDirectory | WorkspaceFile:
    """List a directory or read a file relative to the thread's repository."""
    output = await _run_workspace_script(thread_id, login, email, "path", path)
    return _parse_workspace_output(_WORKSPACE_PATH_ADAPTER, thread_id, output)


async def get_dashboard_thread_file_index(
    thread_id: str, login: str, *, email: str | None = None
) -> WorkspaceFileIndex:
    """List every non-ignored file in the thread's repository."""
    output = await _run_workspace_script(thread_id, login, email, "index", "")
    return _parse_workspace_output(_WORKSPACE_INDEX_ADAPTER, thread_id, output)
