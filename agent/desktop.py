"""Where a thread's commands run.

Cloud threads execute in a per-thread sandbox; local threads execute on the
workstation that created them, reached through the local runner relay. The
location lives in thread metadata rather than in the thread's identity, so a
thread can later be handed from one to the other without being recreated.

``run_location`` is deliberately not ``environment``: that name already belongs
to the named sandbox snapshots in ``agent.dashboard.environments``.
"""

import asyncio
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents.backends.filesystem import FilesystemBackend
from langgraph_sdk import get_client

from .local_runner import LocalMachineBackend
from .run_location import (
    CLOUD_RUN_LOCATION,
    LOCAL_RUN_LOCATION,
    is_local_run,
    run_location,
)
from .utils.json_types import as_json_object

logger = logging.getLogger(__name__)

__all__ = [
    "CLOUD_RUN_LOCATION",
    "LOCAL_RUN_LOCATION",
    "LocalRunTarget",
    "create_local_backend",
    "is_local_run",
    "load_thread_metadata",
    "local_artifact_routes",
    "resolve_local_run_target",
    "resolve_run_metadata",
    "run_location",
]


@dataclass(frozen=True)
class LocalRunTarget:
    """The device and project a local thread is bound to."""

    login: str
    device_id: str
    device_name: str
    project_path: str


def resolve_local_run_target(metadata: dict[str, Any]) -> LocalRunTarget:
    """Read a local thread's binding, or say what the thread is missing.

    Every field comes from thread metadata, which the dashboard stamps from the
    session at creation — never from a run's ``configurable``, which a client
    can set and which would otherwise let one account aim commands at another
    account's machine.

    Nothing here authorizes the project path. The cloud server cannot see the
    user's approved-project list, so the desktop re-checks the path against its
    own allowlist before running anything; this only carries the request.
    """
    login = metadata.get("run_location_login")
    device_id = metadata.get("device_id")
    project_path = metadata.get("local_project_path")
    missing = [
        name
        for name, value in (
            ("run_location_login", login),
            ("device_id", device_id),
            ("local_project_path", project_path),
        )
        if not isinstance(value, str) or not value
    ]
    if missing:
        raise ValueError(f"Local thread metadata is missing {', '.join(missing)}")
    device_name = metadata.get("device_name")
    return LocalRunTarget(
        login=str(login),
        device_id=str(device_id),
        device_name=str(device_name)
        if isinstance(device_name, str) and device_name
        else str(device_id),
        project_path=str(project_path),
    )


def create_local_backend(metadata: dict[str, Any], thread_id: str) -> LocalMachineBackend:
    target = resolve_local_run_target(metadata)
    return LocalMachineBackend(
        login=target.login,
        device_id=target.device_id,
        thread_id=thread_id,
        project_path=target.project_path,
    )


async def load_thread_metadata(thread_id: str) -> dict[str, Any]:
    try:
        thread = await get_client().threads.get(thread_id)
    except Exception:
        logger.exception("Failed to read metadata for thread %s", thread_id)
        return {}
    return as_json_object(thread.get("metadata") if isinstance(thread, dict) else None)


async def resolve_run_metadata(config: dict[str, Any], thread_id: str) -> dict[str, Any]:
    """Thread metadata for the run, without an extra round trip when avoidable.

    The run config usually carries the thread's metadata inline, but that copy
    is best-effort. A run whose ``configurable`` claims to be local is refetched
    so the device binding comes from what the dashboard stamped rather than from
    the run request: the inline copy is only trusted to say *cloud*, which is
    also what a thread predating ``run_location`` correctly reports.
    """
    metadata = as_json_object((config or {}).get("metadata"))
    if is_local_run(metadata):
        return metadata
    configurable = as_json_object((config or {}).get("configurable"))
    if configurable.get("run_location") == LOCAL_RUN_LOCATION:
        return await load_thread_metadata(thread_id)
    return metadata


ARTIFACT_ROUTE_NAMES = ("large_tool_results", "conversation_history")


def _artifacts_root() -> Path:
    """Where the agent's scratch files go.

    ``tempfile.gettempdir()`` probes the filesystem — ``os.getcwd()`` among
    other calls — the first time it runs, so this belongs off the event loop.
    Its caller does that; do not call it directly from async code.
    """
    configured = os.environ.get("OPEN_SWE_LOCAL_ARTIFACTS_DIR")
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / f"open-swe-artifacts-{os.getuid()}"


def _build_artifact_routes(thread_id: str) -> dict[str, FilesystemBackend]:
    # The thread id becomes a path segment, so it may only be a plain name.
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "-", thread_id or "thread").lstrip(".") or "thread"
    root = _artifacts_root() / safe_id
    routes: dict[str, FilesystemBackend] = {}
    for name in ARTIFACT_ROUTE_NAMES:
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        routes[f"/{name}/"] = FilesystemBackend(root_dir=directory, virtual_mode=True)
    return routes


async def local_artifact_routes(thread_id: str) -> dict[str, FilesystemBackend]:
    """Backends for the agent's own scratch files on a local run.

    Offloaded tool results and evicted history default to the artifacts root,
    which for a local run is the user's project: the dumps would be relayed to
    their machine, show up as changes, and be swept into the next `git add -A`.
    Route them out of the repository while leaving the virtual paths the model
    sees unchanged.

    Every filesystem touch happens in one worker thread: this runs inside the
    graph factory, on the event loop the whole deployment shares.
    """
    return await asyncio.to_thread(_build_artifact_routes, thread_id)
