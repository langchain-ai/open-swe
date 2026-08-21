"""Where a sandbox keeps the tree we clone repositories into.

The provider answers first, since it knows what it booted; the sandbox's own
shell answers when the provider doesn't. Either way the answer only counts once
it is confirmed writable, and it is cached on the thread's handle — which drops
it when the backend behind it is replaced.
"""

import logging
import posixpath
import shlex
from collections.abc import AsyncIterable

from deepagents.backends.protocol import SandboxBackendProtocol

from .providers import current_sandbox_provider
from .proxy import SandboxBackendProxy, unwrap_sandbox_backend

logger = logging.getLogger(__name__)


async def aresolve_repo_dir(sandbox_backend: SandboxBackendProtocol, repo_name: str) -> str:
    """Resolve the repository directory for a sandbox backend."""
    if not repo_name:
        raise ValueError("repo_name must be a non-empty string")

    work_dir = await aresolve_sandbox_work_dir(sandbox_backend)
    return posixpath.join(work_dir, repo_name)


async def aresolve_sandbox_work_dir(sandbox_backend: SandboxBackendProtocol) -> str:
    """Resolve a writable base directory for repository operations."""
    proxy = sandbox_backend if isinstance(sandbox_backend, SandboxBackendProxy) else None
    if proxy is not None and proxy.work_dir:
        return proxy.work_dir

    checked_candidates: list[str] = []
    async for candidate in _iter_work_dir_candidates(sandbox_backend):
        checked_candidates.append(candidate)
        if await _is_writable_directory(sandbox_backend, candidate):
            if proxy is not None:
                proxy.cache_work_dir(candidate)
            return candidate

    msg = "Failed to resolve a writable sandbox work directory"
    if checked_candidates:
        msg = f"{msg}. Candidates checked: {', '.join(checked_candidates)}"
    raise RuntimeError(msg)


async def _iter_work_dir_candidates(
    sandbox_backend: SandboxBackendProtocol,
) -> AsyncIterable[str]:
    seen: set[str] = set()

    provider_work_dir = _normalize_path(await _provider_work_dir(sandbox_backend))
    if provider_work_dir:
        seen.add(provider_work_dir)
        yield provider_work_dir

    # Only reached when the provider had no answer or its answer was rejected,
    # so these shells run at most once per resolution.
    for command in ("pwd", "printf '%s' \"$HOME\""):
        candidate = await _resolve_shell_path(sandbox_backend, command)
        if candidate and candidate not in seen:
            seen.add(candidate)
            yield candidate


async def _provider_work_dir(sandbox_backend: SandboxBackendProtocol) -> str | None:
    """Ask the configured provider, handing it the real backend.

    A proxy is a handle, not a sandbox: providers inspect the object their own
    SDK produced, so passing the wrapper would make every provider answer None.
    """
    try:
        return await current_sandbox_provider().work_dir(unwrap_sandbox_backend(sandbox_backend))
    except Exception:
        logger.debug("Provider could not report its sandbox work dir", exc_info=True)
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
