"""A sandbox backend whose commands run on the user's own machine."""

import base64
import logging
from typing import Any

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from .broker import (
    DEFAULT_COMMAND_TIMEOUT_S,
    CommandRelay,
    LocalDeviceUnreachableError,
    runner_broker,
)

logger = logging.getLogger(__name__)

_SYNC_UNSUPPORTED = "LocalMachineBackend is async-only; use the a-prefixed method instead."


class LocalMachineBackend(BaseSandbox):
    """Relays every operation to the desktop that owns the thread's project.

    ``BaseSandbox`` derives reads, writes, edits, greps and globs from
    ``aexecute``, so the relay only has to carry shell commands and whole-file
    transfers. Nothing here decides *whether* a path may be touched: the desktop
    validates the project against the user's own allowlist before it runs
    anything, because it is the only side that can see that allowlist.
    """

    enable_capture_offload = True

    def __init__(
        self,
        *,
        login: str,
        device_id: str,
        thread_id: str,
        project_path: str,
        broker: CommandRelay | None = None,
    ) -> None:
        self._login = login
        self._device_id = device_id
        self._thread_id = thread_id
        self._project_path = project_path
        self._broker = broker or runner_broker
        self._default_timeout = DEFAULT_COMMAND_TIMEOUT_S

    @property
    def id(self) -> str:
        return self._device_id

    async def _call(self, frame: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        return await self._broker.call(
            self._login,
            self._device_id,
            {
                **frame,
                "thread_id": self._thread_id,
                "device_id": self._device_id,
                "project_path": self._project_path,
            },
            timeout=timeout,
        )

    async def aexecute(  # noqa: ASYNC109
        self, command: str, *, timeout: int | None = None
    ) -> ExecuteResponse:
        effective = timeout if timeout is not None else self._default_timeout
        reply = await self._call(
            {"type": "exec", "command": command, "timeout": effective},
            # Outlast the desktop's own timer so a command that overruns comes
            # back as its own output rather than as an unreachable device.
            timeout=effective + 30,
        )
        return ExecuteResponse(
            output=str(reply.get("output") or ""),
            exit_code=reply.get("exit_code"),
            truncated=bool(reply.get("truncated")),
        )

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        reply = await self._call(
            {
                "type": "upload",
                "files": [
                    {"path": path, "content": base64.b64encode(content).decode("ascii")}
                    for path, content in files
                ],
            },
            timeout=self._default_timeout,
        )
        results = reply.get("results")
        if not isinstance(results, list) or len(results) != len(files):
            return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]
        return [
            FileUploadResponse(path=path, error=(result or {}).get("error"))
            for (path, _), result in zip(files, results, strict=True)
        ]

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        reply = await self._call(
            {"type": "download", "paths": paths},
            timeout=self._default_timeout,
        )
        results = reply.get("results")
        if not isinstance(results, list) or len(results) != len(paths):
            return [FileDownloadResponse(path=path, error="file_not_found") for path in paths]
        responses = []
        for path, result in zip(paths, results, strict=True):
            entry = result or {}
            error = entry.get("error")
            content = entry.get("content")
            responses.append(
                FileDownloadResponse(
                    path=path,
                    content=None
                    if error or not isinstance(content, str)
                    else base64.b64decode(content),
                    error=error,
                )
            )
        return responses

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        raise NotImplementedError(_SYNC_UNSUPPORTED)


__all__ = ["LocalDeviceUnreachableError", "LocalMachineBackend"]
