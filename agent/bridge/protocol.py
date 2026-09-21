"""The wire shapes a bridge request and its reply travel in.

Both halves are built against these: the CLI reads ``params`` and writes
``result``, and the backend turns a ``result`` back into the deepagents
dataclass the agent expects. Nothing here touches the database.
"""

import base64
import binascii
from typing import Literal, Self

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from pydantic import BaseModel, ConfigDict, JsonValue

BridgeMethod = Literal["execute", "upload_files", "download_files"]

JsonObject = dict[str, JsonValue]


class BridgeParams(BaseModel):
    """What the CLI is asked to do. Unknown keys are rejected: the CLI reads these."""

    model_config = ConfigDict(extra="forbid")

    def dump(self) -> JsonObject:
        return self.model_dump(mode="json")


class ExecuteParams(BridgeParams):
    command: str
    timeout: int | None = None


class UploadFileParam(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content_base64: str


class UploadFilesParams(BridgeParams):
    files: list[UploadFileParam]

    @classmethod
    def of(cls, files: list[tuple[str, bytes]]) -> Self:
        return cls(
            files=[
                UploadFileParam(path=path, content_base64=base64.b64encode(content).decode())
                for path, content in files
            ]
        )


class DownloadFilesParams(BridgeParams):
    paths: list[str]


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
