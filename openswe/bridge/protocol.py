"""The wire shapes a bridge request travels in.

The CLI reads ``params`` and writes ``result``; the backend side that turns a
``result`` back into deepagents dataclasses lives in ``openswe.bridge.backend``,
so the web app can import these without loading the agent stack.
"""

import base64
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue

BridgeMethod = Literal[
    "execute", "upload_files", "download_files", "worktree_handoff", "local_action"
]

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


class WorktreeHandoffParams(BridgeParams):
    branch: str
    base_ref: str | None = None
    start_from_origin: bool = True
    user_checkout: bool = False


class LocalActionParams(BridgeParams):
    expires_at: float = 0
    operation: Literal["read", "write", "execute", "import"]
    path: str | None = Field(default=None, max_length=4096)
    content: str | None = Field(default=None, max_length=65536)
    command: str | None = Field(default=None, max_length=65536)
    cwd: str | None = Field(default=None, max_length=4096)
    timeout: int = Field(default=120, ge=1, le=300)
    # The thread asking, which the desktop scopes grants to and names in its dialog.
    thread_id: str | None = None
    thread_title: str | None = Field(default=None, max_length=200)
    # The runtime's review: only a cleared call may run inside a grant without asking.
    cleared: bool = False
    concern: str | None = Field(default=None, max_length=64)
