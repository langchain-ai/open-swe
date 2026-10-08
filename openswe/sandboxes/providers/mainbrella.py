"""Async Mainbrella adapter using the generation-bound container API."""

import asyncio
import logging
import posixpath
import re
import shlex
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx2
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from pydantic import BaseModel, Field

from openswe.config import ENV

logger = logging.getLogger(__name__)

_SYNC_UNSUPPORTED = "MainbrellaSandbox is async-only; use the a-prefixed method instead."
_CREATE_TIMEOUT_SECONDS = 120
_POLL_INTERVAL_SECONDS = 1
_MAX_FILE_BYTES = 1024 * 1024
_MAX_EXECUTE_TIMEOUT_SECONDS = 900


class _Container(BaseModel):
    id: str
    created_at: str = Field(alias="createdAt")
    status: str


class _Creation(_Container):
    container_id: str = Field(alias="containerId")


class _Account(BaseModel):
    containers: list[_Container]
    creation: _Creation | None = None


class _CommandResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int | None = Field(alias="exitCode")
    timed_out: bool = Field(alias="timedOut")
    output_truncated: bool = Field(alias="outputTruncated")

    def response(self) -> ExecuteResponse:
        output = self.stdout
        if self.stderr:
            output += ("\n" if output and not output.endswith("\n") else "") + self.stderr
        if self.timed_out:
            output += "\nCommand timed out."
        return ExecuteResponse(
            output=output,
            exit_code=124 if self.timed_out else self.exit_code,
            truncated=self.output_truncated,
        )


class _Execution(_CommandResult):
    id: UUID
    status: Literal[
        "starting",
        "running",
        "succeeded",
        "failed",
        "canceled",
        "timed_out",
        "interrupted",
        "output_limit",
    ]
    stdout: str = ""
    stderr: str = ""


class MainbrellaProvider:
    def __init__(self) -> None:
        api_key = ENV.MAINBRELLA_API_KEY.optional()
        if not api_key:
            raise ValueError("MAINBRELLA_API_KEY environment variable is required")
        url = urlsplit(ENV.MAINBRELLA_API_URL.get())
        if (
            not url.hostname
            or url.username
            or url.password
            or url.path not in ("", "/")
            or url.query
            or url.fragment
            or (
                url.scheme != "https"
                and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"})
            )
        ):
            raise ValueError("MAINBRELLA_API_URL must be an HTTPS origin or loopback HTTP origin")
        self._base_url = f"{url.scheme}://{url.netloc}"
        self._api_key = api_key
        self._image_id = ENV.MAINBRELLA_IMAGE_ID.optional()
        self._catalog_id = ENV.MAINBRELLA_CATALOG_ID.get()
        self._size = ENV.MAINBRELLA_SANDBOX_SIZE.get()
        if self._size not in {"lite", "small", "medium", "large", "xl"}:
            raise ValueError("MAINBRELLA_SANDBOX_SIZE must be lite, small, medium, large or xl")

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, str | int] | None = None,
        content: bytes | None = None,
        idempotency_key: str | None = None,
        timeout: float = 90,
    ) -> httpx2.Response:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if content is not None:
            headers["Content-Type"] = "application/octet-stream"
        async with httpx2.AsyncClient(
            base_url=self._base_url, headers=headers, timeout=timeout, follow_redirects=False
        ) as client:
            response = await client.request(method, path, params=params, json=body, content=content)
            response.raise_for_status()
            return response

    async def connect(self, sandbox_id: str) -> MainbrellaSandbox:
        slot, separator, created_at = sandbox_id.removeprefix("mainbrella:").partition("@")
        if not sandbox_id.startswith("mainbrella:") or not separator:
            raise ValueError(
                "Invalid Mainbrella sandbox ID; expected mainbrella:<slot>@<createdAt>"
            )
        backend = MainbrellaSandbox(self, slot, created_at)
        response = await self.request("GET", "/containers")
        account = _Account.model_validate_json(response.content)
        if not any(
            container.id == slot
            and container.created_at == created_at
            and container.status == "running"
            for container in account.containers
        ):
            raise RuntimeError(f"Mainbrella sandbox {sandbox_id} is no longer running")
        return backend

    async def create(self) -> MainbrellaSandbox:
        key = str(uuid4())
        selection = (
            {"imageId": self._image_id} if self._image_id else {"catalogId": self._catalog_id}
        )
        body: dict[str, str | int] = {**selection, "size": self._size}
        deadline = asyncio.get_running_loop().time() + _CREATE_TIMEOUT_SECONDS
        while (remaining := deadline - asyncio.get_running_loop().time()) > 0:
            try:
                response = await self.request(
                    "POST",
                    "/containers",
                    body=body,
                    idempotency_key=key,
                    timeout=min(90, remaining),
                )
            except (httpx2.TransportError, httpx2.HTTPStatusError) as exc:
                if isinstance(exc, httpx2.HTTPStatusError) and exc.response.status_code != 503:
                    raise
                logger.warning(
                    "Mainbrella creation response unavailable",
                    extra={"creation_key": key},
                    exc_info=True,
                )
            else:
                account = _Account.model_validate_json(response.content)
                creation = account.creation
                if creation is None or creation.status not in {"starting", "running"}:
                    raise RuntimeError(
                        f"Invalid Mainbrella creation response (creation key: {key})"
                    )
                if creation.status == "running":
                    if not any(
                        container.id == creation.container_id
                        and container.created_at == creation.created_at
                        and container.status == "running"
                        for container in account.containers
                    ):
                        raise RuntimeError(
                            f"Mainbrella creation is unconfirmed (creation key: {key})"
                        )
                    return MainbrellaSandbox(self, creation.container_id, creation.created_at)
            await asyncio.sleep(
                min(_POLL_INTERVAL_SECONDS, max(0, deadline - asyncio.get_running_loop().time()))
            )
        raise TimeoutError(f"Mainbrella creation is ambiguous; reconcile creation key {key}")


class MainbrellaSandbox(BaseSandbox):
    def __init__(self, provider: MainbrellaProvider, slot: str, created_at: str) -> None:
        if not re.fullmatch(r"small|c[1-9]\d{0,2}", slot):
            raise ValueError("Invalid Mainbrella container slot")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", created_at):
            raise ValueError("Invalid Mainbrella container generation")
        timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z") != created_at:
            raise ValueError("Invalid Mainbrella container generation")
        self._provider = provider
        self._slot = slot
        self._created_at = created_at

    @property
    def id(self) -> str:
        return f"mainbrella:{self._slot}@{self._created_at}"

    @property
    def _params(self) -> dict[str, str]:
        return {"id": self._slot, "createdAt": self._created_at}

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        seconds = timeout if timeout is not None else 60
        if not 0 < seconds <= _MAX_EXECUTE_TIMEOUT_SECONDS:
            raise ValueError("Mainbrella command timeout must be between 1 and 900 seconds")
        # Provider exec inherits PATH alone; git's global config needs the guest user's home.
        command = (
            'export HOME="${HOME:-$(getent passwd "$(id -u)" | cut -d: -f6)}"\n'
            "cd /workspace || exit 1\n" + command
        )
        if seconds <= 60:
            response = await self._provider.request(
                "POST",
                "/containers/exec",
                params=self._params,
                body={"command": command, "timeoutMs": seconds * 1000},
                timeout=seconds + 30,
            )
            return _CommandResult.model_validate_json(response.content).response()
        return await self._execute_managed(command, seconds)

    async def _execute_managed(self, command: str, seconds: int) -> ExecuteResponse:
        response = await self._provider.request(
            "POST",
            "/containers/executions",
            params=self._params,
            body={"command": command, "timeoutMs": seconds * 1000},
            idempotency_key=str(uuid4()),
        )
        execution = _Execution.model_validate_json(response.content)
        path = f"/containers/executions/{execution.id}"
        try:
            async with asyncio.timeout(seconds + 30):
                while True:
                    response = await self._provider.request("GET", path, params=self._params)
                    execution = _Execution.model_validate_json(response.content)
                    if execution.status not in {"starting", "running"}:
                        return execution.response()
                    await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError, Exception:
            try:
                await self._provider.request("DELETE", path, params=self._params, timeout=10)
            except Exception:
                logger.warning(
                    "Mainbrella execution cancellation failed",
                    extra={"execution_id": str(execution.id)},
                    exc_info=True,
                )
            raise

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        results: list[FileUploadResponse] = []
        for path, content in files:
            if not self._valid_file_path(path):
                results.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            if len(content) > _MAX_FILE_BYTES:
                results.append(FileUploadResponse(path=path, error="file_too_large"))
                continue
            try:
                parent = posixpath.dirname(path)
                mkdir = await self.aexecute(f"mkdir -p -- {shlex.quote(parent)}")
                if mkdir.exit_code != 0:
                    results.append(FileUploadResponse(path=path, error="permission_denied"))
                    continue
                await self._provider.request(
                    "PUT",
                    "/containers/files",
                    params={**self._params, "path": path},
                    content=content,
                )
            except Exception as exc:
                logger.warning(
                    "Mainbrella file upload failed", extra={"file_path": path}, exc_info=True
                )
                results.append(FileUploadResponse(path=path, error=self._file_error(exc)))
            else:
                results.append(FileUploadResponse(path=path))
        return results

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        raise NotImplementedError(_SYNC_UNSUPPORTED)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        results: list[FileDownloadResponse] = []
        for path in paths:
            try:
                response = await self._provider.request(
                    "GET", "/containers/files", params={**self._params, "path": path}
                )
            except Exception as exc:
                logger.warning(
                    "Mainbrella file download failed", extra={"file_path": path}, exc_info=True
                )
                results.append(FileDownloadResponse(path=path, error=self._file_error(exc)))
            else:
                results.append(FileDownloadResponse(path=path, content=response.content))
        return results

    @staticmethod
    def _valid_file_path(path: str) -> bool:
        return (
            path.startswith("/")
            and "\0" not in path
            and len(path.encode("utf-8")) <= 4096
            and all(part and part not in {".", ".."} for part in path.split("/")[1:])
        )

    @staticmethod
    def _file_error(exc: Exception) -> str:
        if isinstance(exc, httpx2.HTTPStatusError):
            return {
                400: "invalid_path",
                403: "permission_denied",
                404: "file_not_found",
                413: "file_too_large",
            }.get(exc.response.status_code, "Mainbrella file transfer failed")
        return "Mainbrella file transfer unavailable"


async def create_mainbrella_sandbox(sandbox_id: str | None = None) -> MainbrellaSandbox:
    provider = MainbrellaProvider()
    return await provider.connect(sandbox_id) if sandbox_id else await provider.create()
