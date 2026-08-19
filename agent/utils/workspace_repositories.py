import asyncio
import logging
import posixpath
import shlex
from typing import TypedDict
from urllib.parse import urlsplit, urlunsplit

from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)


class WorkspaceRepository(TypedDict):
    path: str
    remotes: dict[str, str]


def _sanitize_remote_url(raw_url: str) -> str:
    url = "".join(character for character in raw_url.strip() if character >= " ")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return "<invalid remote URL>"
    if not parsed.scheme or not hostname:
        return url

    if ":" in hostname:
        hostname = f"[{hostname}]"
    if port is not None:
        hostname = f"{hostname}:{port}"
    if parsed.scheme not in {"http", "https"} and parsed.username:
        hostname = f"{parsed.username}@{hostname}"
    return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))


def _parse_repository(output: str) -> WorkspaceRepository | None:
    lines = output.splitlines()
    if not lines:
        return None

    path = posixpath.normpath(lines[0].strip())
    if not path.startswith("/"):
        return None

    remotes: dict[str, str] = {}
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 2:
            continue
        name, url = fields[:2]
        if name not in remotes:
            remotes[name] = _sanitize_remote_url(url)
    return {"path": path, "remotes": remotes}


async def discover_workspace_repositories(
    sandbox_backend: SandboxBackendProtocol,
    work_dir: str,
) -> list[WorkspaceRepository]:
    try:
        listing = await sandbox_backend.als(work_dir)
    except Exception:
        logger.warning("Failed to list workspace repositories", exc_info=True)
        return []
    if listing.error:
        logger.warning("Failed to list workspace repositories: %s", listing.error)
        return []

    normalized_work_dir = posixpath.normpath(work_dir)
    directories: list[str] = []
    for entry in listing.entries or []:
        raw_path = entry.get("path")
        if not entry.get("is_dir") or not isinstance(raw_path, str):
            continue
        path = posixpath.normpath(raw_path)
        if not path.startswith("/"):
            path = posixpath.normpath(posixpath.join(normalized_work_dir, path))
        if posixpath.dirname(path) == normalized_work_dir:
            directories.append(path)

    commands = [
        sandbox_backend.aexecute(
            " && ".join(
                (
                    f"git -C {shlex.quote(directory)} rev-parse --show-toplevel",
                    f"git -C {shlex.quote(directory)} remote -v",
                )
            )
        )
        for directory in sorted(set(directories))
    ]
    results = await asyncio.gather(*commands, return_exceptions=True)

    repositories: dict[str, WorkspaceRepository] = {}
    for result in results:
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            logger.warning("Failed to inspect a workspace repository: %s", result)
            continue
        if result.exit_code != 0:
            continue
        repository = _parse_repository(result.output)
        if repository is not None:
            repositories[repository["path"]] = repository
    return [repositories[path] for path in sorted(repositories)]
