"""The wire shapes a bridge request travels in.

The CLI reads ``params`` and writes ``result``; the backend side that turns a
``result`` back into deepagents dataclasses lives in ``agent.bridge.backend``,
so the web app can import these without loading the agent stack.
"""

import base64
from typing import Literal, Self

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
