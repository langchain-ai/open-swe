"""Read-only Hub routes and native skill discovery scoped to the current run."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Final, cast

from deepagents.backends.context_hub import ContextHubBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.middleware.skills import SkillsMiddleware, SkillsState, SkillsStateUpdate
from langchain.agents.middleware.types import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from langsmith import Client
from runtime_api.endpoint import call
from runtime_api.skill_hub import SkillHubSettings
from runtime_api.skills import SKILLS, RunSkills, SkillsRequest

from rt.backend import OpenSweBackend, RunLocal
from rt.run_prompt import ThreadPromptMiddleware
from rt.tools import run_id
from rt.tracing import MIDDLEWARE_TRACE_POLICY

logger = logging.getLogger(__name__)
SKILL_ROUTES: Final = ("/my-skills/", "/organization-skills/", "/skills/")
_READ_ONLY: Final = "Skills are read-only here"


class _NamedRepos:
    def __init__(self) -> None:
        self.asked = False
        self.skills: RunSkills | None = None
        self.lock = asyncio.Lock()


class RunSkillRepos:
    """The skill repositories the backend names for the run, asked once per run.

    The backend names them so that every Robin environment sharing the workspace keeps its
    own repositories, and so personal ones follow its private-thread check.
    """

    def __init__(self, backend: OpenSweBackend) -> None:
        self.backend = backend
        self._runs = RunLocal(_NamedRepos)

    async def named(self) -> RunSkills | None:
        run = self._runs.get(self.backend.run)
        async with run.lock:
            if not run.asked:
                run.asked = True
                try:
                    run.skills = await call(self.backend, SKILLS, SkillsRequest(run_id=run_id()))
                except Exception:
                    logger.exception("Cannot ask the backend for the run's skills; mounting none")
        return run.skills


class _HubRun:
    def __init__(self) -> None:
        self.opened = False
        self.backend: ContextHubBackend | None = None
        self.lock = asyncio.Lock()


class HubSkillRoute(BackendProtocol):
    """A static route whose Hub snapshot and personal authority belong to one run."""

    def __init__(self, repos: RunSkillRepos, *, personal: bool) -> None:
        self._repos = repos
        self._personal = personal
        self._runs = RunLocal(_HubRun)

    async def _opened(self) -> ContextHubBackend | None:
        run = self._runs.get(self._repos.backend.run)
        async with run.lock:
            if run.opened:
                return run.backend
            run.opened = True
            try:
                named = await self._repos.named()
                repo = (
                    None
                    if named is None
                    else named.personal_repo
                    if self._personal
                    else named.organization_repo
                )
                if repo is None:
                    return None
                settings = await SkillHubSettings.from_env()
                client = Client(
                    api_url=settings.endpoint,
                    api_key=settings.api_key.get_secret_value(),
                    workspace_id=str(settings.workspace_id),
                    auto_batch_tracing=False,
                )
                hub = ContextHubBackend(repo, client=client)
                try:
                    listing = await hub.als("/")
                finally:
                    await asyncio.to_thread(client.close)
                if listing.error:
                    logger.warning(
                        "Cannot open skills Hub snapshot", extra={"hub_error": listing.error}
                    )
                    return None
                run.backend = hub
            except Exception:
                logger.exception("Cannot open run-scoped skills Hub; denying access")
        return run.backend

    async def als(self, path: str) -> LsResult:
        hub = await self._opened()
        return await hub.als(path) if hub is not None else LsResult(entries=[])

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        hub = await self._opened()
        return (
            await hub.aread(file_path, offset, limit)
            if hub is not None
            else ReadResult(error="Skill access denied")
        )

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        hub = await self._opened()
        return (
            await hub.agrep(pattern, path, glob, max_count=max_count)
            if hub is not None
            else GrepResult(matches=[])
        )

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        hub = await self._opened()
        return await hub.aglob(pattern, path) if hub is not None else GlobResult(matches=[])

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        hub = await self._opened()
        return (
            await hub.adownload_files(paths)
            if hub is not None
            else [FileDownloadResponse(path=path, error="permission_denied") for path in paths]
        )

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error=_READ_ONLY, path=file_path)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return EditResult(error=_READ_ONLY, path=file_path)

    async def adelete(self, file_path: str) -> DeleteResult:
        return DeleteResult(error=_READ_ONLY, path=file_path)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error="permission_denied") for path, _ in files]


class RunSkillsMiddleware(SkillsMiddleware):
    """Use native discovery and pinning, replacing checkpointed metadata every run."""

    name = "SkillsMiddleware"
    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(self, files: BackendProtocol, *, thread_prompt: bool = False) -> None:
        self._thread_prompt = ThreadPromptMiddleware() if thread_prompt else None
        super().__init__(backend=files, sources=list(SKILL_ROUTES))

    async def abefore_agent(
        self, state: SkillsState, runtime: Runtime, config: RunnableConfig
    ) -> SkillsStateUpdate | None:
        return await super().abefore_agent(
            cast(SkillsState, {**state, "skills_metadata": None}), runtime, config
        )

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse | ExtendedModelResponse:
        if self._thread_prompt is not None:
            handler = partial(self._thread_prompt.awrap_model_call, handler=handler)
        return await super().awrap_model_call(request, handler)
