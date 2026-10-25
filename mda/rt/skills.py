"""The run's skills, as the backend lists them for it (``runtime_api.skills``).

This runtime keeps them as read-only files, lists them in each model call's system
prompt with deepagents' own skills middleware, the agent's and its subagents', and
answers the model's reads of them. A thread's checkpoint keeps the skills it
listed, so each run lists them again: a public thread never keeps the skills of a
person a previous run acted for.
"""

import asyncio
from collections.abc import Callable, Mapping
from typing import Any, Final, cast

from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileData,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.state import StateBackend
from deepagents.backends.utils import create_file_data
from deepagents.middleware.skills import SkillsMiddleware, SkillsState, SkillsStateUpdate
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime_api.endpoint import Transport, call
from runtime_api.operations import RunRequest
from runtime_api.skills import SKILLS

_SYNC: Final = "The run's skills are read asynchronously"
_READ_ONLY: Final = "Skills are read-only here"


class _Listed(StateBackend):
    """deepagents' in-memory file reads, over the skills the backend listed."""

    def __init__(self, files: Mapping[str, FileData]) -> None:
        super().__init__()
        self._listed = dict(files)

    def _read_files(self) -> dict[str, Any]:
        return dict(self._listed)


class SkillFiles(BackendProtocol):
    """The run's skills as files: a folder per skill under each source, with its ``SKILL.md``.

    The backend lists them on the run's first read of any; every read after that is
    answered here.
    """

    def __init__(self, transport: Transport, run_id: Callable[[], str | None]) -> None:
        self._transport = transport
        self._run_id = run_id
        self._files: _Listed | None = None
        self._listing = asyncio.Lock()

    async def _listed(self) -> _Listed:
        async with self._listing:
            if self._files is None:
                listed = await call(self._transport, SKILLS, RunRequest(run_id=self._run_id()))
                self._files = _Listed(
                    {skill.path: create_file_data(skill.content) for skill in listed.files}
                )
        return self._files

    async def als(self, path: str) -> LsResult:
        return (await self._listed()).ls(path)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return (await self._listed()).read(file_path, offset, limit)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return (await self._listed()).grep(pattern, path, glob, max_count=max_count)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return (await self._listed()).glob(pattern, path)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return (await self._listed()).download_files(paths)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY, path=file_path)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,  # noqa: FBT001, FBT002
    ) -> EditResult:
        return EditResult(error=_READ_ONLY, path=file_path)

    async def adelete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=_READ_ONLY, path=file_path)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]

    def ls(self, path: str) -> LsResult:
        raise NotImplementedError(_SYNC)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        raise NotImplementedError(_SYNC)


class RunSkillsMiddleware(SkillsMiddleware):
    """deepagents' skills middleware, listing the run's skills afresh every run."""

    async def abefore_agent(
        self, state: SkillsState, runtime: Runtime, config: RunnableConfig
    ) -> SkillsStateUpdate | None:
        return await super().abefore_agent(
            cast(SkillsState, {**state, "skills_metadata": None}), runtime, config
        )
