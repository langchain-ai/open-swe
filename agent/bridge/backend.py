"""A sandbox backend whose filesystem is the machine running the CLI.

``BaseSandbox`` derives ls/read/write/edit/grep/glob/delete from execute plus
the two file transfers, so only those three round trips are implemented here.
Each one queues a request, waits for the CLI to answer it, and turns the answer
back into the deepagents dataclass the agent expects.

A waiter subscribes before it enqueues, so the wake-up it is waiting for cannot
land in the gap between the two, and it re-reads the row on every liveness tick
anyway — a notification that never arrives costs latency only. The liveness tick
is also what keeps a run from waiting out a five-minute command timeout on a
machine that went away.
"""

import asyncio
import base64
import binascii
import logging
from collections.abc import AsyncIterator
from typing import Self

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from pydantic import BaseModel

from agent.bridge import listener
from agent.bridge.constants import (
    CLOSED_EVENT,
    DEFAULT_EXECUTE_TIMEOUT_SECONDS,
    WAIT_GRACE_SECONDS,
)
from agent.bridge.protocol import (
    BridgeMethod,
    BridgeParams,
    DownloadFilesParams,
    ExecuteParams,
    JsonObject,
    UploadFilesParams,
)
from agent.bridge.store import Bridge, BridgeStore, BridgeUnavailableError
from agent.sandboxes.state import SandboxUnreachableError

logger = logging.getLogger(__name__)

_SYNC_UNSUPPORTED = "BridgeSandboxBackend is async-only; use the a-prefixed method instead."
_LIVENESS_TICK_SECONDS = 15.0
_FILE_TRANSFER_TIMEOUT_SECONDS = 120


class ExecuteResult(BaseModel):
    output: str
    exit_code: int | None = None
    truncated: bool = False

    def response(self) -> ExecuteResponse:
        return ExecuteResponse(
            output=self.output, exit_code=self.exit_code, truncated=self.truncated
        )


class UploadFileResult(BaseModel):
    path: str
    error: str | None = None


class UploadFilesResult(BaseModel):
    responses: list[UploadFileResult]

    def response(self) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=entry.path, error=entry.error) for entry in self.responses]


class DownloadFileResult(BaseModel):
    path: str
    content_base64: str | None = None
    error: str | None = None

    def response(self) -> FileDownloadResponse:
        if self.content_base64 is None:
            return FileDownloadResponse(path=self.path, content=None, error=self.error)
        try:
            content = base64.b64decode(self.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            return FileDownloadResponse(
                path=self.path, content=None, error=f"undecodable content: {exc}"
            )
        return FileDownloadResponse(path=self.path, content=content, error=self.error)


class DownloadFilesResult(BaseModel):
    responses: list[DownloadFileResult]

    def response(self) -> list[FileDownloadResponse]:
        return [entry.response() for entry in self.responses]


class BridgeSandboxBackend(BaseSandbox):
    """The thread's sandbox, reached through the CLI's long poll."""

    def __init__(self, *, bridge_id: str, thread_id: str | None = None) -> None:
        self._bridge_id = bridge_id
        self._thread_id = thread_id

    @classmethod
    async def connect(cls, thread_id: str, bridge_id: str) -> Self:
        """Bind to a live bridge, or refuse the run — never replace it."""
        backend = cls(bridge_id=bridge_id, thread_id=thread_id)
        if not await backend._is_alive():
            raise SandboxUnreachableError(thread_id, backend.id, "bridge disconnected")
        return backend

    @property
    def bridge_id(self) -> str:
        return self._bridge_id

    @property
    def id(self) -> str:
        return Bridge.sandbox_id_for(self._bridge_id)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        command_timeout = timeout if timeout is not None else DEFAULT_EXECUTE_TIMEOUT_SECONDS
        result = await self._round_trip(
            "execute",
            ExecuteParams(command=command, timeout=command_timeout),
            wait=command_timeout + WAIT_GRACE_SECONDS,
        )
        return ExecuteResult.model_validate(result).response()

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        result = await self._round_trip(
            "upload_files",
            UploadFilesParams.of(files),
            wait=_FILE_TRANSFER_TIMEOUT_SECONDS + WAIT_GRACE_SECONDS,
        )
        return UploadFilesResult.model_validate(result).response()

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        result = await self._round_trip(
            "download_files",
            DownloadFilesParams(paths=paths),
            wait=_FILE_TRANSFER_TIMEOUT_SECONDS + WAIT_GRACE_SECONDS,
        )
        return DownloadFilesResult.model_validate(result).response()

    async def _is_alive(self) -> bool:
        bridge = await BridgeStore.load(self._bridge_id)
        return bridge is not None and bridge.is_alive

    def _unreachable(self, cause: str) -> SandboxUnreachableError:
        return SandboxUnreachableError(self._thread_id or "<unknown>", self.id, cause)

    async def _round_trip(
        self, method: BridgeMethod, params: BridgeParams, *, wait: float
    ) -> JsonObject:
        try:
            async with listener.subscribe(self._bridge_id) as events:
                request_id = await BridgeStore.enqueue(
                    self._bridge_id, method=method, params=params.dump()
                )
                return await self._await_result(events, request_id, wait=wait)
        except BridgeUnavailableError as exc:
            raise self._unreachable(str(exc)) from exc

    async def _await_result(
        self,
        events: AsyncIterator[listener.BridgeEvent],
        request_id: str,
        *,
        wait: float,
    ) -> JsonObject:
        deadline = asyncio.get_running_loop().time() + wait
        while True:
            outcome = await self._finished_outcome(request_id)
            if outcome is not None:
                return outcome
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                await BridgeStore.complete(
                    self._bridge_id, request_id, error="timed out waiting for the local sandbox"
                )
                raise TimeoutError(
                    f"Sandbox bridge {self._bridge_id} did not answer within {wait:.0f}s"
                )
            if not await self._is_alive():
                # Closing fails the request in the same transaction that marks the
                # bridge closed, so a re-read carries the real reason.
                outcome = await self._finished_outcome(request_id)
                if outcome is not None:
                    return outcome
                raise self._unreachable("bridge disconnected")
            try:
                async with asyncio.timeout(min(_LIVENESS_TICK_SECONDS, remaining)):
                    async for woken_id, event in events:
                        if event == CLOSED_EVENT or woken_id == request_id:
                            break
            except TimeoutError:
                continue

    async def _finished_outcome(self, request_id: str) -> JsonObject | None:
        outcome = await BridgeStore.outcome(self._bridge_id, request_id)
        if outcome is None:
            raise self._unreachable("request disappeared before it was answered")
        if not outcome.finished:
            return None
        if outcome.error is not None:
            raise RuntimeError(outcome.error)
        if outcome.result is None:
            raise RuntimeError("bridge answered without a result")
        return outcome.result
