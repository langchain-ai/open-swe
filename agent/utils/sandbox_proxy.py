"""A stable per-thread handle in front of a sandbox backend that can be replaced.

Knows nothing about where backends come from or who else holds the handle: the
reconnect and the registry publish are both handed in, so a proxy can be driven
— and tested — on its own.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

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

logger = logging.getLogger(__name__)

Reconnect = Callable[[], Awaitable[SandboxBackendProtocol]]

_SYNC_UNSUPPORTED = "SandboxBackendProxy is async-only; use the a-prefixed method instead."


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
        reconnect: Reconnect | None = None,
        publish: Callable[["SandboxBackendProxy"], None] | None = None,
    ) -> None:
        self._backend = backend
        self._thread_id = thread_id
        self._reconnect = reconnect
        self._publish = publish
        self._work_dir: str | None = None
        self._startup_task: asyncio.Task[SandboxBackendProtocol] | None = None
        self._lock: asyncio.Lock | None = None

    @property
    def current(self) -> SandboxBackendProtocol:
        return self._get_backend()

    @property
    def id(self) -> str:
        return self._get_backend().id

    def replace_backend(self, backend: SandboxBackendProtocol) -> None:
        self._backend = backend
        self._startup_task = None
        # A different sandbox is a different filesystem: whatever work dir the
        # previous one resolved to says nothing about this one.
        self._work_dir = None

    @property
    def has_backend(self) -> bool:
        return self._backend is not None

    @property
    def work_dir(self) -> str | None:
        """The work dir resolved for the backend currently behind this handle."""
        return self._work_dir

    def cache_work_dir(self, work_dir: str) -> None:
        self._work_dir = work_dir

    def cancel_startup(self) -> None:
        if self._startup_task is not None:
            self._startup_task.cancel()

    def set_reconnect(self, reconnect: Reconnect | None) -> None:
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
                logger.info("Reconnecting sandbox backend for thread %s", self._thread_id)
                self.start()
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
                self.replace_backend(unwrap_sandbox_backend(sandbox_backend))
                if self._publish is not None:
                    self._publish(self)
            backend = self._backend
            if backend is None:
                raise RuntimeError(f"No sandbox backend cached for thread {self._thread_id}")
            return backend

    def ls(self, path: str) -> LsResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def als(self, path: str) -> LsResult:
        return await (await self._aget_backend()).als(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
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
        return await (await self._aget_backend()).agrep(pattern, path, glob, max_count=max_count)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await (await self._aget_backend()).aglob(pattern, path)

    def write(self, file_path: str, content: str) -> WriteResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
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
        return await (await self._aget_backend()).aedit(
            file_path, old_string, new_string, replace_all
        )

    def delete(self, file_path: str) -> DeleteResult:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def adelete(self, file_path: str) -> DeleteResult:
        return await (await self._aget_backend()).adelete(file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await (await self._aget_backend()).aupload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await (await self._aget_backend()).adownload_files(paths)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return await (await self._aget_backend()).aexecute(command, timeout=timeout)

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
        backend = await self._aget_backend()
        offload = getattr(backend, "aexecute_with_offload", None)
        if offload is None:
            return ExecuteOffloadResult(
                offloaded=False, response=await self._aplain(backend, command, timeout)
            )
        return await offload(
            command,
            capture_path,
            max_inline_bytes=max_inline_bytes,
            max_capture_bytes=max_capture_bytes,
            timeout=timeout,
        )

    @staticmethod
    async def _aplain(
        backend: SandboxBackendProtocol, command: str, timeout: int | None
    ) -> ExecuteResponse:
        if timeout is not None and execute_accepts_timeout(type(backend)):
            return await backend.aexecute(command, timeout=timeout)
        return await backend.aexecute(command)


def unwrap_sandbox_backend(sandbox_backend: SandboxBackendProtocol) -> SandboxBackendProtocol:
    if isinstance(sandbox_backend, SandboxBackendProxy):
        return sandbox_backend.current
    return sandbox_backend
