import asyncio
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from deepagents.backends import LocalShellBackend
from deepagents.backends.filesystem import FilesystemBackend

SHELL_ENV_KEYS = ("HOME", "LANG", "LC_ALL", "PATH", "SHELL", "TMPDIR")


def is_desktop_run(configurable: dict[str, Any]) -> bool:
    return configurable.get("source") == "desktop"


def resolve_desktop_project(configurable: dict[str, Any]) -> str:
    """The thread's own git worktree, which is the only place a desktop run may run.

    The desktop app checks every thread out into its own worktree under
    `OPEN_SWE_LOCAL_WORKTREES_DIR`, so containment in that directory is what
    makes a path trustworthy — and what keeps concurrent runs off each other's
    working tree.
    """
    requested = configurable.get("local_project_path")
    worktrees_dir = os.environ.get("OPEN_SWE_LOCAL_WORKTREES_DIR")
    if not isinstance(requested, str) or not requested or not worktrees_dir:
        raise ValueError("Desktop runs require a local worktree path")
    project = Path(os.path.realpath(requested))
    if Path(os.path.realpath(worktrees_dir)) not in project.parents or not project.is_dir():
        raise ValueError("local_project_path is not an agent worktree")
    return str(project)


def create_desktop_backend(configurable: dict[str, Any]) -> LocalShellBackend:
    return LocalShellBackend(
        root_dir=resolve_desktop_project(configurable),
        virtual_mode=False,
        env={key: value for key in SHELL_ENV_KEYS if (value := os.environ.get(key))},
    )


def _artifacts_root() -> Path:
    configured = os.environ.get("OPEN_SWE_LOCAL_ARTIFACTS_DIR")
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / f"open-swe-artifacts-{os.getuid()}"


async def desktop_artifact_routes(thread_id: str) -> dict[str, FilesystemBackend]:
    """Backends for the agent's own scratch files on a desktop run.

    Offloaded tool results and evicted history default to the artifacts root,
    which for a desktop run is the user's project: the dumps would show up as
    changes and be swept into the next `git add -A`. Route them out of the
    repository while leaving the virtual paths the model sees unchanged.
    """
    # The thread id becomes a path segment, so it may only be a plain name.
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "-", thread_id or "thread").lstrip(".") or "thread"
    root = _artifacts_root() / safe_id
    routes = {}
    for name in ("large_tool_results", "conversation_history"):
        directory = root / name
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        routes[f"/{name}/"] = await asyncio.to_thread(
            FilesystemBackend, root_dir=directory, virtual_mode=True
        )
    return routes
