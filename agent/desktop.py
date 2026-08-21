import json
import os
from pathlib import Path
from typing import Any

from deepagents.backends import LocalShellBackend

from .config import local_projects_file

# Process-level environment handed to the desktop shell, not app configuration.
SHELL_ENV_KEYS = ("HOME", "LANG", "LC_ALL", "PATH", "SHELL", "TMPDIR")


def is_desktop_run(configurable: dict[str, Any]) -> bool:
    return configurable.get("source") == "desktop"


def resolve_desktop_project(configurable: dict[str, Any]) -> str:
    requested = configurable.get("local_project_path")
    allowlist_path = local_projects_file()
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
        virtual_mode=True,
        env={key: value for key in SHELL_ENV_KEYS if (value := os.environ.get(key))},
    )
