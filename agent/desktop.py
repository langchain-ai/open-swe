import asyncio
import json
import os
import re
import tempfile
from pathlib import Path

from deepagents.backends import LocalShellBackend
from deepagents.backends.filesystem import FilesystemBackend

from agent.config import ENV
from agent.run_config import RunConfig

SHELL_ENV_KEYS = ("HOME", "LANG", "LC_ALL", "PATH", "SHELL", "TMPDIR")


def is_desktop_run(cfg: RunConfig) -> bool:
    return cfg.source == "desktop"


def _allowed_projects() -> set[str]:
    allowlist_path = ENV.OPEN_SWE_LOCAL_PROJECTS_FILE.optional()
    if not allowlist_path:
        return set()
    with open(allowlist_path, encoding="utf-8") as file:
        entries = json.load(file)
    if not isinstance(entries, list):
        raise ValueError("OPEN_SWE_LOCAL_PROJECTS_FILE must contain a JSON array")
    return {
        os.path.realpath(entry["cwd"] if isinstance(entry, dict) else entry)
        for entry in entries
        if isinstance(entry, str) or (isinstance(entry, dict) and isinstance(entry.get("cwd"), str))
    }


def is_desktop_worktree(path: str) -> bool:
    """Whether the path is a worktree the desktop app created for a thread."""
    worktrees_dir = ENV.OPEN_SWE_LOCAL_WORKTREES_DIR.optional()
    if not worktrees_dir:
        return False
    return Path(os.path.realpath(worktrees_dir)) in Path(os.path.realpath(path)).parents


def resolve_desktop_project(cfg: RunConfig) -> str:
    """The directory a desktop run may work in.

    A thread runs either in a project the user registered in the desktop app or
    in its own git worktree, which the app checks out under
    `OPEN_SWE_LOCAL_WORKTREES_DIR`. Both are named by the same config value, so
    a path is trustworthy when it is allowlisted or contained in that directory.
    """
    requested = cfg.local_project_path
    if not requested:
        raise ValueError("Desktop runs require a local project path")
    project = Path(os.path.realpath(requested))
    if not project.is_dir() or not (
        is_desktop_worktree(requested) or str(project) in _allowed_projects()
    ):
        raise ValueError("local_project_path is not an allowed project directory")
    return str(project)


def create_desktop_backend(cfg: RunConfig) -> LocalShellBackend:
    return LocalShellBackend(
        root_dir=resolve_desktop_project(cfg),
        virtual_mode=False,
        env={key: value for key in SHELL_ENV_KEYS if (value := os.environ.get(key))},
    )


def _artifacts_root() -> Path:
    configured = ENV.OPEN_SWE_LOCAL_ARTIFACTS_DIR.optional()
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
