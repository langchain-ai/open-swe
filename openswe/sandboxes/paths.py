"""Helpers for resolving portable writable paths inside sandboxes."""

import logging
import posixpath
import shlex
from collections.abc import AsyncIterable, Iterable

from deepagents.backends.protocol import SandboxBackendProtocol

from openswe.bridge.constants import SANDBOX_ID_PREFIX
from openswe.sandboxes.state import SandboxBackendProxy

logger = logging.getLogger(__name__)

_WORK_DIR_CACHE_ATTR = "_open_swe_resolved_work_dir"
_PROVIDER_ATTR_NAMES = ("sandbox", "_sandbox", "_backend")
WORKSPACE_DIR = "/workspace"


def _is_local_checkout(sandbox_backend: SandboxBackendProtocol) -> bool:
    """A bridged machine's working directory is the user's checkout itself."""
    sandbox_id = getattr(sandbox_backend, "id", None)
    return isinstance(sandbox_id, str) and sandbox_id.startswith(SANDBOX_ID_PREFIX)


async def resolve_repo_dir(sandbox_backend: SandboxBackendProtocol, repo_name: str) -> str:
    """Resolve the repository directory for a sandbox backend."""
    if not repo_name:
        raise ValueError("repo_name must be a non-empty string")

    work_dir = await resolve_sandbox_work_dir(sandbox_backend)
    if _is_local_checkout(sandbox_backend):
        return work_dir
    return posixpath.join(work_dir, repo_name)


async def resolve_checkout_dir(
    sandbox_backend: SandboxBackendProtocol, work_dir: str, repo_name: str
) -> str:
    """Resolve a checkout from its prepared directory, workspace, or legacy home."""
    if _is_local_checkout(sandbox_backend):
        return work_dir
    repo_dir = posixpath.join(work_dir, repo_name)
    workspace_repo = shlex.quote(posixpath.join(WORKSPACE_DIR, repo_name))
    name = shlex.quote(posixpath.basename(repo_name))
    result = await sandbox_backend.aexecute(
        f"test -e {shlex.quote(repo_dir)}/.git || "
        f"{{ test -e {workspace_repo}/.git && printf %s {workspace_repo}; }} || "
        f'{{ test -e "$HOME"/{name}/.git && printf %s "$HOME"/{name}; }}'
    )
    checkout_dir = _normalize_path(result.output) if result.exit_code == 0 else None
    return checkout_dir or repo_dir


async def resolve_sandbox_work_dir(sandbox_backend: SandboxBackendProtocol) -> str:
    """Resolve a writable base directory for repository operations."""
    cached_work_dir = getattr(sandbox_backend, _WORK_DIR_CACHE_ATTR, None)
    declared = declared_work_dir(sandbox_backend)
    if (
        isinstance(cached_work_dir, str)
        and cached_work_dir
        and (not declared or cached_work_dir == declared)
    ):
        return cached_work_dir

    local = _is_local_checkout(await _connected(sandbox_backend))
    checked_candidates: list[str] = []
    async for candidate in _iter_work_dir_candidates(sandbox_backend, workspace=not local):
        checked_candidates.append(candidate)
        if await _is_writable_directory(sandbox_backend, candidate):
            # A remote fallback is re-probed so a later /workspace replaces it.
            if local or candidate in (WORKSPACE_DIR, declared_work_dir(sandbox_backend)):
                _cache_work_dir(sandbox_backend, candidate)
            return candidate

    msg = "Failed to resolve a writable sandbox work directory"
    if checked_candidates:
        msg = f"{msg}. Candidates checked: {', '.join(checked_candidates)}"
    raise RuntimeError(msg)


async def _connected(sandbox_backend: SandboxBackendProtocol) -> SandboxBackendProtocol:
    """The backend behind a proxy, connected so its id is known."""
    return (
        await sandbox_backend.ready()
        if isinstance(sandbox_backend, SandboxBackendProxy)
        else sandbox_backend
    )


async def _iter_work_dir_candidates(
    sandbox_backend: SandboxBackendProtocol, *, workspace: bool
) -> AsyncIterable[str]:
    seen: set[str] = set()

    for candidate in _iter_provider_paths(sandbox_backend, "get_work_dir"):
        if candidate not in seen:
            seen.add(candidate)
            yield candidate

    if workspace and WORKSPACE_DIR not in seen:
        seen.add(WORKSPACE_DIR)
        yield WORKSPACE_DIR

    shell_work_dir = await _resolve_shell_path(sandbox_backend, "pwd")
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

    shell_home_dir = await _resolve_shell_path(sandbox_backend, "printf '%s' \"$HOME\"")
    if shell_home_dir and shell_home_dir not in seen:
        seen.add(shell_home_dir)
        yield shell_home_dir


def _iter_provider_paths(
    sandbox_backend: SandboxBackendProtocol,
    *method_names: str,
) -> Iterable[str]:
    for provider in _iter_path_providers(sandbox_backend):
        for method_name in method_names:
            path = _call_path_method(provider, method_name)
            if path:
                yield path


def _iter_path_providers(sandbox_backend: SandboxBackendProtocol) -> Iterable[object]:
    yield sandbox_backend
    for attr_name in _PROVIDER_ATTR_NAMES:
        provider = getattr(sandbox_backend, attr_name, None)
        if provider is not None:
            yield provider


def _call_path_method(provider: object, method_name: str) -> str | None:
    method = getattr(provider, method_name, None)
    if not callable(method):
        return None

    try:
        value = method()
        return _normalize_path(value if isinstance(value, str) else None)
    except Exception:
        logger.debug("Failed to call %s on %s", method_name, type(provider).__name__, exc_info=True)
        return None


async def _resolve_shell_path(
    sandbox_backend: SandboxBackendProtocol,
    command: str,
) -> str | None:
    result = await sandbox_backend.aexecute(command)
    if result.exit_code != 0:
        return None
    return _normalize_path(result.output)


def _normalize_path(raw_path: str | None) -> str | None:
    if raw_path is None:
        return None

    path = raw_path.strip()
    if not path or not path.startswith("/"):
        return None

    return posixpath.normpath(path)


async def _is_writable_directory(
    sandbox_backend: SandboxBackendProtocol,
    directory: str,
) -> bool:
    safe_directory = shlex.quote(directory)
    result = await sandbox_backend.aexecute(f"test -d {safe_directory} && test -w {safe_directory}")
    return result.exit_code == 0


def forget_work_dir(sandbox_backend: object) -> None:
    """Drop a cached work dir once the checkout behind the backend has moved."""
    vars(sandbox_backend).pop(_WORK_DIR_CACHE_ATTR, None)


def declared_work_dir(sandbox_backend: SandboxBackendProtocol) -> str | None:
    """The work dir the sandbox's provider names, before any probing."""
    return next(iter(_iter_provider_paths(sandbox_backend, "get_work_dir")), None)


def remember_work_dir(sandbox_backend: SandboxBackendProtocol, work_dir: str) -> None:
    """Record a work dir already known to be writable, so no run probes it again."""
    _cache_work_dir(sandbox_backend, work_dir)


def _cache_work_dir(sandbox_backend: SandboxBackendProtocol, work_dir: str) -> None:
    try:
        setattr(sandbox_backend, _WORK_DIR_CACHE_ATTR, work_dir)
    except Exception:
        logger.debug("Failed to cache sandbox work dir on %s", type(sandbox_backend).__name__)
