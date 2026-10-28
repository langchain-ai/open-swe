"""Check what a shell command would publish to a public repository before it runs.

The sandbox's GitHub credentials are injected on the wire, so once a ``git push``
or ``gh`` write runs nothing on the backend sees it; a pull-request-time check is
too late because the pushed branch is already public. This guard reads a push's
commits and added lines, and the text of a ``gh`` write, while the command is
still only a tool call.
"""

import json
import logging
from collections.abc import Awaitable, Callable, Mapping

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from openswe.confidentiality.check import GuardMode, Publication, repository_is_public
from openswe.confidentiality.outgoing import github_cli_writes, push_segments
from openswe.middleware.git_push import (
    PushInspectionError,
    chained_push_dirs,
    origin_repository,
    parse_git_push,
    push_range,
    run_git,
)
from openswe.middleware.pr_creation_guard import shell_commands, shell_tokens
from openswe.middleware.trace import OpenSWEMiddleware
from openswe.run_config import RunConfig
from openswe.sandboxes.state import SANDBOX_BACKENDS

logger = logging.getLogger(__name__)

Handler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]]


def _run_config(request: ToolCallRequest) -> RunConfig:
    config = getattr(getattr(request, "runtime", None), "config", None)
    return RunConfig.from_config(config if isinstance(config, Mapping) else get_config())


def _refusal(request: ToolCallRequest, content: str) -> ToolMessage:
    return ToolMessage(
        content=content, tool_call_id=request.tool_call.get("id") or "", status="error"
    )


def _error(error_type: str, error: str) -> str:
    return json.dumps({"status": "error", "error_type": error_type, "error": error})


class ConfidentialityGuardMiddleware(OpenSWEMiddleware):
    """Refuse, or in shadow mode only log, likely confidential content bound for a public repo."""

    state_schema = AgentState

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Handler
    ) -> ToolMessage | Command:
        mode = GuardMode.current()
        commands = shell_commands(request)
        if mode == GuardMode.OFF or not commands:
            return await handler(request)
        cfg = _run_config(request)
        backend = SANDBOX_BACKENDS.get(cfg.thread_id) if cfg.thread_id else None
        try:
            publications = [
                publication
                for command in commands
                for publication in await self._publications(backend, command)
            ]
        except PushInspectionError as exc:
            logger.info(
                "Could not inspect a publication for confidential content",
                extra={"confidentiality_mode": mode.value, "thread_id": cfg.thread_id},
            )
            if mode == GuardMode.ENFORCE:
                return _refusal(request, _error("ConfidentialityInspectionError", str(exc)))
            return await handler(request)
        for publication in publications:
            verdict = await publication.check(cfg)
            if not verdict.allowed:
                return _refusal(request, verdict.refusal(publication))
        return await handler(request)

    async def _publications(
        self, backend: SandboxBackendProtocol | None, command: str
    ) -> list[Publication]:
        if backend is None:
            raise PushInspectionError("The sandbox is not connected, so nothing can be inspected.")
        publications: list[Publication] = []
        if (parsed := parse_git_push(command)) is not None:
            pushed = await push_range(backend, parsed)
            if pushed is None:
                raise PushInspectionError(
                    "Cannot tell what this push would publish. Push the current branch to the "
                    "same branch on origin. Nothing ran."
                )
            publications.append(
                Publication(
                    await origin_repository(backend, pushed.root),
                    "push",
                    await push_segments(backend, pushed),
                )
            )
        for repo_dir in chained_push_dirs(command):
            await self._refuse_chained_public_push(backend, repo_dir)
        for write in github_cli_writes(shell_tokens(command)):
            repository = write.repository or await self._origin(backend, write.repo_dir)
            publications.append(
                Publication(repository, "github_cli", await write.with_body_files(backend))
            )
        return publications

    async def _origin(self, backend: SandboxBackendProtocol, repo_dir: str | None) -> str | None:
        root = await run_git(backend, repo_dir, "rev-parse --show-toplevel")
        return await origin_repository(backend, root.output) if root.ok else None

    async def _refuse_chained_public_push(
        self, backend: SandboxBackendProtocol, repo_dir: str | None
    ) -> None:
        repository = await self._origin(backend, repo_dir)
        if repository is None or await repository_is_public(repository):
            raise PushInspectionError(
                "This command pushes to a public repository as part of a longer command, so "
                "Open SWE cannot inspect what it publishes. Nothing ran. Commit first, then run "
                "the push on its own (for example `git -C <repo> push -u origin <branch>`)."
            )
