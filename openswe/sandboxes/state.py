"""Shared sandbox state used by server and middleware."""

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Generator, Sequence
from contextlib import contextmanager
from typing import Any

from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    ExecuteOffloadResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
    execute_accepts_timeout,
)
from deepagents.backends.sandbox import BaseSandbox

from openswe.github.token_scope import token_repositories_from_metadata
from openswe.threads.store import THREADS
from openswe.utils.thread_reads import read_thread

logger = logging.getLogger(__name__)


class SandboxUnreachableError(RuntimeError):
    """The thread's sandbox did not answer this run.

    Says nothing about whether it will answer the next one — a later run
    reconnects to the same id and may well succeed. It is never resolved by
    creating a replacement: the sandbox holds the agent's only copy of its
    working tree, so a fresh one would discard uncommitted work while the agent
    carried on believing it was still there.
    """

    def __init__(
        self, thread_id: str, sandbox_id: str | None, cause: str, *, reason: str | None = None
    ) -> None:
        self.thread_id = thread_id
        self.sandbox_id = sandbox_id
        # What the sandbox service answered, safe to show the person.
        self.reason = reason
        detail = f"{cause} ({reason})" if reason else cause
        super().__init__(
            f"Sandbox {sandbox_id or '<unknown>'} for thread {thread_id} is unreachable: {detail}"
        )


class SandboxDeletedError(SandboxUnreachableError):
    """LangSmith confirmed the thread's sandbox is deleted: nothing replaces it, so the thread
    can never run again, and a new thread is the way on."""


class SandboxHandedOverError(SandboxUnreachableError):
    """The thread's old sandbox is gone or set aside: its next run starts in a fresh one MDA opens."""


_SYNC_UNSUPPORTED = "SandboxBackendProxy is async-only; use the a-prefixed method instead."
_DEFAULT_EXECUTE_TIMEOUT_SECONDS = 300


class SandboxBackendProxy(BaseSandbox):
    """Stable per-thread backend handle whose target can be replaced.

    Subclasses ``BaseSandbox`` (not just the protocol) so ``FilesystemMiddleware``
    recognizes it as capture-at-source capable: its ``_resolve_capture`` gates the
    ``execute`` offload path on ``isinstance(backend, BaseSandbox)``. Without this
    the tool falls back to plain ``execute`` and the command's entire stdout is
    pulled into the worker process, bypassing the in-sandbox size cap.
    """

    def __init__(
        self,
        backend: SandboxBackendProtocol | None = None,
        *,
        thread_id: str | None = None,
        reconnect: Callable[[], Awaitable[SandboxBackendProtocol]] | None = None,
    ) -> None:
        self._backend = backend
        self._thread_id = thread_id
        self._reconnect = reconnect
        self._startup_task: asyncio.Task[SandboxBackendProtocol] | None = None
        self._lock: asyncio.Lock | None = None
        self._active_calls = 0
        self.last_used_at = time.monotonic()

    def touch(self) -> None:
        self.last_used_at = time.monotonic()

    @property
    def busy(self) -> bool:
        return (
            self._active_calls > 0
            or (self._startup_task is not None and not self._startup_task.done())
            or (self._lock is not None and self._lock.locked())
        )

    @property
    def backend_id(self) -> str | None:
        return self._backend.id if self._backend is not None else None

    @contextmanager
    def _use(self) -> Generator[None]:
        self._active_calls += 1
        self.touch()
        try:
            yield
        finally:
            self._active_calls -= 1
            self.touch()

    @property
    def current(self) -> SandboxBackendProtocol:
        return self._get_backend()

    @property
    def id(self) -> str:
        return self._get_backend().id

    def get_work_dir(self) -> str | None:
        """Expose the current backend's declared directory once connected."""
        from openswe.sandboxes.paths import declared_work_dir

        return declared_work_dir(self._backend) if self._backend is not None else None

    def replace_backend(self, backend: SandboxBackendProtocol) -> None:
        self._backend = backend
        self._startup_task = None

    def detach(self) -> None:
        """Forget the sandbox; the next call connects to whatever the thread is bound to then."""
        self._backend = None
        self._startup_task = None

    def follow(self, sandbox_id: object) -> None:
        """Forget a cached sandbox the thread is no longer bound to, on whichever replica holds it.

        A thread moved off a Mac, or handed to MDA, is bound to a new sandbox by another
        process; this one would otherwise keep using the sandbox the thread left.
        """
        if self._backend is not None and self._backend.id != sandbox_id:
            self.detach()

    @property
    def has_backend(self) -> bool:
        return self._backend is not None

    def set_reconnect(
        self,
        reconnect: Callable[[], Awaitable[SandboxBackendProtocol]] | None,
    ) -> None:
        self._reconnect = reconnect

    def start(self) -> None:
        if self._startup_task is not None:
            if not self._startup_task.cancelled():
                return
            self._startup_task = None
        if self._reconnect is None:
            if self._backend is not None:
                return
            raise RuntimeError("Cannot start sandbox without a reconnect callback")
        self._startup_task = asyncio.ensure_future(self._reconnect())
        self._startup_task.add_done_callback(self._startup_completed)

    def _startup_completed(self, task: asyncio.Task[SandboxBackendProtocol]) -> None:
        if task.cancelled():
            logger.warning("Sandbox startup was cancelled for thread %s", self._thread_id)
            return
        exception = task.exception()
        if exception is not None:
            logger.warning(
                "Sandbox startup failed for thread %s: %s",
                self._thread_id,
                exception,
            )

    async def ready(self) -> SandboxBackendProtocol:
        with self._use():
            return await self._aget_backend()

    def _get_backend(self) -> SandboxBackendProtocol:
        if self._backend is None:
            suffix = f" for thread {self._thread_id}" if self._thread_id else ""
            raise RuntimeError(f"No sandbox backend cached{suffix}")
        return self._backend

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _aget_backend(self) -> SandboxBackendProtocol:
        if self._backend is not None and self._startup_task is None:
            return self._backend
        if not self._thread_id:
            raise RuntimeError("No sandbox backend cached")

        async with self._get_lock():
            if self._backend is not None and self._startup_task is None:
                return self._backend
            if self._startup_task is None:
                if self._reconnect is not None:
                    logger.info("Reconnecting sandbox backend for thread %s", self._thread_id)
                    self.start()
                else:
                    sandbox_id = await get_sandbox_id_from_metadata(self._thread_id)
                    if not sandbox_id:
                        raise ValueError(
                            f"Missing sandbox_id in thread metadata for {self._thread_id}"
                        )

                    logger.info(
                        "Reconnecting sandbox backend for thread %s from metadata", self._thread_id
                    )
                    from openswe.sandboxes.connect import connect_sandbox

                    self._startup_task = asyncio.create_task(connect_sandbox(sandbox_id))
                    self._startup_task.add_done_callback(self._startup_completed)
            startup_task = self._startup_task
            if startup_task is None:
                raise RuntimeError(f"Sandbox startup task missing for thread {self._thread_id}")

        try:
            sandbox_backend = await asyncio.shield(startup_task)
        except BaseException:
            if startup_task.done():
                async with self._get_lock():
                    if self._startup_task is startup_task:
                        self._startup_task = None
            raise

        async with self._get_lock():
            if self._startup_task is startup_task:
                self._backend = unwrap_sandbox_backend(sandbox_backend)
                remember_connection(self._backend)
                self._startup_task = None
                _SANDBOX_CACHE.put_proxy(self._thread_id, self)
            backend = self._backend
            if backend is None:
                raise RuntimeError(f"No sandbox backend cached for thread {self._thread_id}")
            return backend

    def ls(self, path: str) -> LsResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def als(self, path: str) -> LsResult:
        with self._use():
            return await (await self._aget_backend()).als(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        with self._use():
            return await (await self._aget_backend()).aread(file_path, offset, limit)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        with self._use():
            return await (await self._aget_backend()).agrep(
                pattern, path, glob, max_count=max_count
            )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        with self._use():
            return await (await self._aget_backend()).aglob(pattern, path)

    def write(self, file_path: str, content: str) -> WriteResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        with self._use():
            return await (await self._aget_backend()).awrite(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        with self._use():
            return await (await self._aget_backend()).aedit(
                file_path, old_string, new_string, replace_all
            )

    def delete(self, file_path: str) -> DeleteResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def adelete(self, file_path: str) -> DeleteResult:
        with self._use():
            return await (await self._aget_backend()).adelete(file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        with self._use():
            return await (await self._aget_backend()).aupload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        with self._use():
            return await (await self._aget_backend()).adownload_files(paths)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        with self._use():
            effective_timeout = timeout if timeout is not None else _DEFAULT_EXECUTE_TIMEOUT_SECONDS
            return await (await self._aget_backend()).aexecute(command, timeout=effective_timeout)

    def execute_with_offload(
        self,
        command: str,
        capture_path: str,
        *,
        max_inline_bytes: int,
        max_capture_bytes: int | None = None,
        timeout: int | None = None,
    ) -> ExecuteOffloadResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aexecute_with_offload(
        self,
        command: str,
        capture_path: str,
        *,
        max_inline_bytes: int,
        max_capture_bytes: int | None = None,
        timeout: int | None = None,  # noqa: ASYNC109 - forwarded to backend, not an asyncio contract
    ) -> ExecuteOffloadResult:
        with self._use():
            backend = await self._aget_backend()
            effective_timeout = timeout if timeout is not None else _DEFAULT_EXECUTE_TIMEOUT_SECONDS
            offload = getattr(backend, "aexecute_with_offload", None)
            if offload is None:
                return ExecuteOffloadResult(
                    offloaded=False,
                    response=await self._aplain(backend, command, effective_timeout),
                )
            return await offload(
                command,
                capture_path,
                max_inline_bytes=max_inline_bytes,
                max_capture_bytes=max_capture_bytes,
                timeout=effective_timeout,
            )

    @staticmethod
    async def _aplain(
        backend: SandboxBackendProtocol, command: str, timeout: int | None
    ) -> ExecuteResponse:
        if timeout is not None and execute_accepts_timeout(type(backend)):
            return await backend.aexecute(command, timeout=timeout)
        return await backend.aexecute(command)


_SANDBOX_CACHE_LIMIT = 512
_SANDBOX_IDLE_TTL_SECONDS = 1800.0


class _SandboxCache:
    def __init__(
        self,
        backends: dict[str, SandboxBackendProxy],
        connections: OrderedDict[str, SandboxBackendProtocol],
        *,
        limit: int = _SANDBOX_CACHE_LIMIT,
        idle_ttl_seconds: float = _SANDBOX_IDLE_TTL_SECONDS,
    ) -> None:
        self.backends = backends
        self.connections = connections
        self.limit = limit
        self.idle_ttl_seconds = idle_ttl_seconds

    def remember_connection(self, sandbox_backend: SandboxBackendProtocol) -> None:
        self.connections[sandbox_backend.id] = sandbox_backend
        self.connections.move_to_end(sandbox_backend.id)

    def put_proxy(self, thread_id: str, proxy: SandboxBackendProxy) -> SandboxBackendProxy:
        proxy.touch()
        self.backends[thread_id] = proxy
        if proxy.backend_id is not None and proxy.backend_id in self.connections:
            self.connections.move_to_end(proxy.backend_id)
        self.evict()
        return proxy

    def touch_thread(self, thread_id: str) -> SandboxBackendProxy | None:
        proxy = self.backends.get(thread_id)
        if proxy is None:
            return None
        proxy.touch()
        if proxy.backend_id is not None and proxy.backend_id in self.connections:
            self.connections.move_to_end(proxy.backend_id)
        self.evict()
        return proxy

    def drop_unreferenced_connection(self, sandbox_id: str | None) -> None:
        if sandbox_id is None:
            return
        if any(proxy.backend_id == sandbox_id for proxy in self.backends.values()):
            return
        self.connections.pop(sandbox_id, None)

    def evict(self) -> None:
        now = time.monotonic()
        expired = [
            thread_id
            for thread_id, proxy in self.backends.items()
            if not proxy.busy and now - proxy.last_used_at >= self.idle_ttl_seconds
        ]
        for thread_id in expired:
            removed = self.backends.pop(thread_id, None)
            if removed is not None:
                self.drop_unreferenced_connection(removed.backend_id)

        idle = sorted(
            (item for item in self.backends.items() if not item[1].busy),
            key=lambda item: item[1].last_used_at,
        )
        for thread_id, _ in idle[: max(0, len(idle) - self.limit)]:
            removed = self.backends.pop(thread_id)
            self.drop_unreferenced_connection(removed.backend_id)

        if len(self.connections) > self.limit:
            referenced = {
                proxy.backend_id for proxy in self.backends.values() if proxy.backend_id is not None
            }
            for sandbox_id in list(self.connections):
                if len(self.connections) <= self.limit:
                    break
                if sandbox_id not in referenced:
                    self.connections.pop(sandbox_id, None)


# Thread ID -> stable SandboxBackendProxy, shared between server.py and middleware.
SANDBOX_BACKENDS: dict[str, SandboxBackendProxy] = {}

# Sandbox ID -> live connection to it. Keyed by sandbox rather than thread so a
# thread rebound on another worker can never be handed the sandbox it left.
SANDBOX_CONNECTIONS: OrderedDict[str, SandboxBackendProtocol] = OrderedDict()

_SANDBOX_CACHE = _SandboxCache(SANDBOX_BACKENDS, SANDBOX_CONNECTIONS)


def remember_connection(sandbox_backend: SandboxBackendProtocol) -> None:
    _SANDBOX_CACHE.remember_connection(sandbox_backend)


def unwrap_sandbox_backend(sandbox_backend: SandboxBackendProtocol) -> SandboxBackendProtocol:
    if isinstance(sandbox_backend, SandboxBackendProxy):
        return sandbox_backend.current
    return sandbox_backend


def set_sandbox_backend(
    thread_id: str,
    sandbox_backend: SandboxBackendProtocol,
) -> SandboxBackendProxy:
    if isinstance(sandbox_backend, SandboxBackendProxy):
        return _SANDBOX_CACHE.put_proxy(thread_id, sandbox_backend)

    existing = SANDBOX_BACKENDS.get(thread_id)
    previous_id = existing.backend_id if existing is not None else None
    remember_connection(sandbox_backend)
    if isinstance(existing, SandboxBackendProxy):
        existing.replace_backend(sandbox_backend)
        if previous_id is not None and previous_id != sandbox_backend.id:
            _SANDBOX_CACHE.drop_unreferenced_connection(previous_id)
        return _SANDBOX_CACHE.put_proxy(thread_id, existing)

    proxy = SandboxBackendProxy(sandbox_backend, thread_id=thread_id)
    return _SANDBOX_CACHE.put_proxy(thread_id, proxy)


def get_or_create_sandbox_backend_proxy(
    thread_id: str,
    *,
    reconnect: Callable[[], Awaitable[SandboxBackendProtocol]] | None = None,
) -> SandboxBackendProxy:
    sandbox_backend = _SANDBOX_CACHE.touch_thread(thread_id)
    if sandbox_backend:
        # Callers that only want the handle pass no callback; keep the one the
        # run registered rather than dropping it to the metadata fallback.
        if reconnect is not None:
            sandbox_backend.set_reconnect(reconnect)
        return sandbox_backend

    sandbox_backend = SandboxBackendProxy(thread_id=thread_id, reconnect=reconnect)
    return _SANDBOX_CACHE.put_proxy(thread_id, sandbox_backend)


async def get_sandbox_metadata(thread_id: str) -> dict[str, Any]:
    """Fetch sandbox metadata from the live thread.

    Never from the run config: a run queued before the thread was bound to another
    sandbox carries the one it has since left.
    """
    # A failed lookup must not read as "unbound": the caller would create a
    # replacement and bind it over the thread's real sandbox.
    thread = await THREADS.get(thread_id)
    if thread is None:
        raise LookupError(f"Thread {thread_id} is not indexed")
    return dict(thread.metadata)


_TOKEN_SCOPES: OrderedDict[str, list[str] | None] = OrderedDict()
_TOKEN_SCOPE_CACHE_LIMIT = 4096


def clear_thread_token_repositories() -> None:
    _TOKEN_SCOPES.clear()


async def thread_token_repositories(thread_id: str) -> list[str] | None:
    """The repositories a thread's GitHub token is limited to; ``None`` is the installation.

    Read from the live thread, never from a run's config: the scope is stamped
    once by the server when the thread is created, and nothing a run carries may
    widen it. It never changes afterwards, so a successful read is cached. A
    failed read propagates, since reading it as "no scope" would widen access.
    """
    if thread_id in _TOKEN_SCOPES:
        _TOKEN_SCOPES.move_to_end(thread_id)
        return _TOKEN_SCOPES[thread_id]
    thread = await read_thread(thread_id)
    metadata = thread.get("metadata") if isinstance(thread, dict) else None
    scope = token_repositories_from_metadata(metadata if isinstance(metadata, dict) else {})
    _TOKEN_SCOPES[thread_id] = scope
    while len(_TOKEN_SCOPES) > _TOKEN_SCOPE_CACHE_LIMIT:
        _TOKEN_SCOPES.popitem(last=False)
    return scope


def narrowed_repositories(
    requested: Sequence[str] | None, recorded: Sequence[str] | None
) -> list[str] | None:
    """A caller's scope, never wider than the one the thread recorded."""
    if recorded is None:
        return None if requested is None else list(requested)
    if requested is None:
        return list(recorded)
    allowed = {repo.lower() for repo in recorded}
    return [repo for repo in requested if repo.lower() in allowed]


async def get_sandbox_id_from_metadata(thread_id: str) -> str | None:
    """Fetch sandbox_id from thread metadata."""
    metadata = await get_sandbox_metadata(thread_id)
    sandbox_id = metadata.get("sandbox_id")
    return sandbox_id if isinstance(sandbox_id, str) else None


async def get_sandbox_backend(thread_id: str) -> SandboxBackendProxy:
    """Get sandbox backend from cache, or connect using thread metadata."""
    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if sandbox_backend is None:
        sandbox_backend = get_or_create_sandbox_backend_proxy(thread_id)
    await sandbox_backend.ready()
    return sandbox_backend
