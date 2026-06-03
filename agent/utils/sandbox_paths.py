"""Helpers for resolving portable writable paths inside sandboxes."""

from __future__ import annotations

import asyncio
import logging
import posixpath
import shlex
from collections.abc import Iterable
from typing import Any

from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)

_WORK_DIR_CACHE_ATTR = "_open_swe_resolved_work_dir"
_PROVIDER_ATTR_NAMES = ("sandbox", "_sandbox")


def resolve_repo_dir(sandbox_backend: SandboxBackendProtocol, repo_name: str) -> str:
    """Resolve the repository directory for a sandbox backend."""
    if not repo_name:
        raise ValueError("repo_name must be a non-empty string")

    work_dir = resolve_sandbox_work_dir(sandbox_backend)
    return posixpath.join(work_dir, repo_name)


async def aresolve_repo_dir(sandbox_backend: SandboxBackendProtocol, repo_name: str) -> str:
    """Async wrapper around resolve_repo_dir for use in event-loop code."""
    return await asyncio.to_thread(resolve_repo_dir, sandbox_backend, repo_name)


def resolve_sandbox_work_dir(sandbox_backend: SandboxBackendProtocol) -> str:
    """Resolve a writable base directory for repository operations."""
    cached_work_dir = getattr(sandbox_backend, _WORK_DIR_CACHE_ATTR, None)
    if isinstance(cached_work_dir, str) and cached_work_dir:
        return cached_work_dir

    checked_candidates: list[str] = []
    for candidate in _iter_work_dir_candidates(sandbox_backend):
        checked_candidates.append(candidate)
        if _is_writable_directory(sandbox_backend, candidate):
            _cache_work_dir(sandbox_backend, candidate)
            return candidate

    msg = "Failed to resolve a writable sandbox work directory"
    if checked_candidates:
        msg = f"{msg}. Candidates checked: {', '.join(checked_candidates)}"
    raise RuntimeError(msg)


async def aresolve_sandbox_work_dir(sandbox_backend: SandboxBackendProtocol) -> str:
    """Async wrapper around resolve_sandbox_work_dir for use in event-loop code."""
    return await asyncio.to_thread(resolve_sandbox_work_dir, sandbox_backend)


def _iter_work_dir_candidates(
    sandbox_backend: SandboxBackendProtocol,
) -> Iterable[str]:
    seen: set[str] = set()

    # Retrieve root_dir directly from local sandbox instance to support correct concurrent workspace resolution
    current = sandbox_backend
    while True:
        if hasattr(current, "current"):
            current = getattr(current, "current")
        elif hasattr(current, "_raw_sandbox"):
            current = getattr(current, "_raw_sandbox")
        else:
            break
    local_dir = None
    if hasattr(current, "root_dir"):
        local_dir = getattr(current, "root_dir")
    elif hasattr(current, "cwd"):
        local_dir = getattr(current, "cwd")

    if local_dir:
        import os
        local_dir = os.path.abspath(str(local_dir))
        if local_dir not in seen:
            seen.add(local_dir)
            yield local_dir



    for candidate in _iter_provider_paths(sandbox_backend, "get_work_dir"):
        if candidate not in seen:
            seen.add(candidate)
            yield candidate

    shell_work_dir = _resolve_shell_path(sandbox_backend, "pwd")
    if shell_work_dir and shell_work_dir not in seen:
        seen.add(shell_work_dir)
        yield shell_work_dir

    for candidate in _iter_provider_paths(
        sandbox_backend,
        "get_user_home_dir",
        "get_user_root_dir",
    ):
        if candidate not in seen:
            seen.add(candidate)
            yield candidate

    shell_home_dir = _resolve_shell_path(sandbox_backend, "printf '%s' \"$HOME\"")
    if shell_home_dir and shell_home_dir not in seen:
        seen.add(shell_home_dir)
        yield shell_home_dir


def _is_local_backend(sandbox_backend: SandboxBackendProtocol) -> bool:
    current = sandbox_backend
    while True:
        if hasattr(current, "current"):
            current = getattr(current, "current")
        elif hasattr(current, "_raw_sandbox"):
            current = getattr(current, "_raw_sandbox")
        else:
            break
    return type(current).__name__ == "LocalShellBackend"


def _iter_provider_paths(
    sandbox_backend: SandboxBackendProtocol,
    *method_names: str,
) -> Iterable[str]:
    is_local = _is_local_backend(sandbox_backend)
    for provider in _iter_path_providers(sandbox_backend):
        for method_name in method_names:
            path = _call_path_method(provider, method_name, is_local)
            if path:
                yield path


def _iter_path_providers(sandbox_backend: SandboxBackendProtocol) -> Iterable[Any]:
    yield sandbox_backend
    for attr_name in _PROVIDER_ATTR_NAMES:
        provider = getattr(sandbox_backend, attr_name, None)
        if provider is not None:
            yield provider


def _call_path_method(provider: Any, method_name: str, is_local: bool) -> str | None:
    method = getattr(provider, method_name, None)
    if not callable(method):
        return None

    try:
        return _normalize_path(method(), is_local)
    except Exception:
        logger.debug("Failed to call %s on %s", method_name, type(provider).__name__, exc_info=True)
        return None


def _resolve_shell_path(
    sandbox_backend: SandboxBackendProtocol,
    command: str,
) -> str | None:
    result = sandbox_backend.execute(command)
    if result.exit_code != 0:
        return None
    is_local = _is_local_backend(sandbox_backend)
    return _normalize_path(result.output, is_local)


def _normalize_path(raw_path: str | None, is_local: bool = False) -> str | None:
    if raw_path is None:
        return None

    path = raw_path.strip()
    if not path:
        return None

    import os
    if os.name == "nt" and is_local:
        return os.path.normpath(path)
    else:
        if not path.startswith("/"):
            return None
        return posixpath.normpath(path)


def _is_writable_directory(
    sandbox_backend: SandboxBackendProtocol,
    directory: str,
) -> bool:
    import os
    is_local = _is_local_backend(sandbox_backend)
    if os.name == "nt" and is_local:
        return os.path.isdir(directory) and os.access(directory, os.W_OK)

    safe_directory = shlex.quote(directory)
    result = sandbox_backend.execute(f"test -d {safe_directory} && test -w {safe_directory}")
    return result.exit_code == 0


def _cache_work_dir(sandbox_backend: SandboxBackendProtocol, work_dir: str) -> None:
    try:
        setattr(sandbox_backend, _WORK_DIR_CACHE_ATTR, work_dir)
    except Exception:
        logger.debug("Failed to cache sandbox work dir on %s", type(sandbox_backend).__name__)
