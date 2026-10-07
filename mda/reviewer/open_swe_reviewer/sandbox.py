"""The run's sandbox, which the Open SWE backend creates and this runtime only attaches to.

The backend owns the sandbox's lifecycle, snapshot and GitHub credentials. File
and shell operations go straight to the sandbox from here, attached by the id
the backend returned when it prepared the run.
"""

import os
from typing import Final

from deepagents.backends import LangSmithSandbox
from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)
from langsmith.sandbox import AsyncSandboxClient

from open_swe_reviewer.backend import current_thread_id

_SANDBOX_API_SUFFIX: Final = "/v2/sandboxes"
_PENDING_ID: Final = "open-swe-run-sandbox"

_sandbox_ids: dict[str, str] = {}
_attached: dict[str, LangSmithSandbox] = {}


def remember_sandbox(thread_id: str, sandbox_id: str) -> None:
    """Record which sandbox the backend prepared for ``thread_id``."""
    _sandbox_ids[thread_id] = sandbox_id


def _client() -> AsyncSandboxClient:
    root = (os.environ.get("LANGSMITH_ENDPOINT") or "https://api.smith.langchain.com").rstrip("/")
    endpoint = root if root.endswith(_SANDBOX_API_SUFFIX) else f"{root}{_SANDBOX_API_SUFFIX}"
    api_key = os.environ.get("OPEN_SWE_SANDBOX_API_KEY") or os.environ.get("LANGSMITH_API_KEY")
    return AsyncSandboxClient(api_endpoint=endpoint, api_key=api_key)


async def _attach() -> LangSmithSandbox:
    sandbox_id = _sandbox_ids.get(current_thread_id())
    if sandbox_id is None:
        raise RuntimeError("The Open SWE backend has not prepared a sandbox for this run")
    attached = _attached.get(sandbox_id)
    if attached is None:
        sandbox = await _client().get_sandbox(name=sandbox_id)
        attached = _attached[sandbox_id] = LangSmithSandbox(sandbox.to_sync())
    return attached


class RunSandboxBackend(SandboxBackendProtocol):
    """Resolves the current run's sandbox on each call; async-only, as the agent is."""

    @property
    def id(self) -> str:
        return _PENDING_ID

    async def als(self, path: str) -> LsResult:
        return await (await _attach()).als(path)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await (await _attach()).aread(file_path, offset, limit)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return await (await _attach()).agrep(pattern, path, glob, max_count=max_count)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return await (await _attach()).aglob(pattern, path)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return await (await _attach()).awrite(file_path, content)

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        return await (await _attach()).aedit(file_path, old_string, new_string, replace_all)

    async def adelete(self, file_path: str) -> DeleteResult:
        return await (await _attach()).adelete(file_path)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return await (await _attach()).aupload_files(files)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return await (await _attach()).adownload_files(paths)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:  # noqa: ASYNC109
        return await (await _attach()).aexecute(command, timeout=timeout)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError("The reviewer runtime only executes asynchronously")
