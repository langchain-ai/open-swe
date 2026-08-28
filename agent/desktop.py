import asyncio
import getpass
import json
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
    requested = configurable.get("local_project_path")
    allowlist_path = os.environ.get("OPEN_SWE_LOCAL_PROJECTS_FILE")
    if not isinstance(requested, str) or not requested or not allowlist_path:
        raise ValueError("Desktop runs require an allowlisted local_project_path")
    with open(allowlist_path, encoding="utf-8") as file:
        entries = json.load(file)
    if not isinstance(entries, list):
        raise ValueError("OPEN_SWE_LOCAL_PROJECTS_FILE must contain a JSON array")
    allowed = {
        os.path.realpath(entry["cwd"] if isinstance(entry, dict) else entry)
        for entry in entries
        if isinstance(entry, str) or (isinstance(entry, dict) and isinstance(entry.get("cwd"), str))
    }
    project = os.path.realpath(requested)
    if project not in allowed or not Path(project).is_dir():
        raise ValueError("local_project_path is not an allowed project directory")
    return project


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
    # os.getuid is POSIX-only; fall back to the login name so the desktop
    # backend still starts on Windows. The value only has to keep two users'
    # artifact roots apart in a shared temp dir.
    getuid = getattr(os, "getuid", None)
    owner = getuid() if getuid else re.sub(r"[^A-Za-z0-9._-]", "-", getpass.getuser())
    return Path(tempfile.gettempdir()) / f"open-swe-artifacts-{owner}"


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
