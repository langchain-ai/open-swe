"""The run's skills, as the backend lists them for it (``runtime_api.skills``).

This runtime keeps them as read-only files, lists them in each model call's system
prompt with deepagents' own skills middleware, the agent's and its subagents', and
answers the model's reads of them. A thread's checkpoint keeps the skills it
listed, so each run lists them again: a public thread never keeps the skills of a
person a previous run acted for.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from functools import cache
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
from deepagents.middleware.skills import (
    SkillsMiddleware,
    SkillsState,
    SkillsStateUpdate,
)
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from runtime_api.endpoint import call
from runtime_api.operations import RunRequest
from runtime_api.skills import SKILLS

from rt.backend import OpenSweBackend, RunLocal
from rt.tracing import MIDDLEWARE_TRACE_POLICY

logger = logging.getLogger(__name__)

_SYNC: Final = "The run's skills are read asynchronously"
_READ_ONLY: Final = "Skills are read-only here"


class _Listed(StateBackend):
    """deepagents' in-memory file reads, over the skills the backend listed."""

    def __init__(self, files: Mapping[str, FileData]) -> None:
        super().__init__()
        self._listed = dict(files)

    def _read_files(self) -> dict[str, Any]:
        return dict(self._listed)


class _Listing:
    """One run's skills, listed once."""

    def __init__(self) -> None:
        self.files: _Listed | None = None
        self.lock = asyncio.Lock()
        self.ahead: asyncio.Task[_Listed] | None = None


class SkillFiles(BackendProtocol):
    """The run's skills as files: a folder per skill under each source, with its ``SKILL.md``.

    The backend lists them on each run's first read of any; every read after that in
    the run is answered here.
    """

    def __init__(self, backend: OpenSweBackend, run_id: Callable[[], str | None]) -> None:
        self._backend = backend
        self._run_id = run_id
        self._listings = RunLocal(_Listing)

    async def _listed(self) -> _Listed:
        listing = self._listings.get(self._backend.run)
        async with listing.lock:
            if listing.files is None:
                listed = await call(self._backend, SKILLS, RunRequest(run_id=self._run_id()))
                listing.files = _Listed(
                    {skill.path: create_file_data(skill.content) for skill in listed.files}
                )
        return listing.files

    def list_ahead(self) -> None:
        """Start listing the run's skills, so its first read finds them listed."""
        listing = self._listings.get(self._backend.run)
        if listing.ahead is not None or not self._backend.run.catalog.skill_sources:
            return
        listing.ahead = asyncio.create_task(self._listed())
        listing.ahead.add_done_callback(_log_failure)

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


def _log_failure(listed: asyncio.Task[_Listed]) -> None:
    if not listed.cancelled() and (error := listed.exception()) is not None:
        # The first read lists them again.
        logger.warning("Could not list the run's skills ahead of its first read", exc_info=error)


@cache
def _labels(sources: tuple[str, ...]) -> tuple[str, ...]:
    """How deepagents labels ``sources`` in the prompt."""
    return tuple(SkillsMiddleware(backend=StateBackend(), sources=list(sources)).source_labels)


class RunSkillsMiddleware(SkillsMiddleware):
    """deepagents' skills middleware over the run's own skill sources, listed afresh every run.

    The graph serves every run, so the sources are read from the run in progress; a run
    whose catalog names none sees no skills, as if the middleware were absent.
    """

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(self, backend: OpenSweBackend, files: SkillFiles) -> None:
        self._run_backend = backend
        super().__init__(backend=files, sources=[])

    @property
    def sources(self) -> list[str]:
        return list(self._run_backend.run.catalog.skill_sources)

    @sources.setter
    def sources(self, _sources: list[str]) -> None:
        """deepagents sets its sources once; these are each run's."""

    @property
    def source_labels(self) -> list[str]:
        return list(_labels(tuple(self.sources)))

    @source_labels.setter
    def source_labels(self, _labels: list[str]) -> None:
        """deepagents sets its labels once; these are each run's."""

    async def abefore_agent(
        self, state: SkillsState, runtime: Runtime, config: RunnableConfig
    ) -> SkillsStateUpdate | None:
        if not self.sources:
            return None
        return await super().abefore_agent(
            cast(SkillsState, {**state, "skills_metadata": None}), runtime, config
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        if not self.sources:
            return await handler(request)
        return await super().awrap_model_call(request, handler)
