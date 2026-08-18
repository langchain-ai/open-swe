import logging
import re
import shlex
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from ..utils.sandbox_paths import aresolve_sandbox_work_dir

logger = logging.getLogger(__name__)

_GITHUB_REMOTE = re.compile(
    r"^(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)([^/\s]+)/([^/\s]+)$"
)
_DISCOVER_TIMEOUT_SECONDS = 15


def github_repo_from_remote(remote: str) -> tuple[str, str] | None:
    value = remote.strip()
    if value.endswith(".git"):
        value = value[:-4]
    match = _GITHUB_REMOTE.fullmatch(value)
    if match is None:
        return None
    return match.group(1), match.group(2)


def _tool_name(request: ToolCallRequest) -> str | None:
    tool_call = getattr(request, "tool_call", None)
    if not isinstance(tool_call, Mapping):
        return None
    name = tool_call.get("name")
    return name if isinstance(name, str) else None


def _output(response: Any) -> str:
    output = getattr(response, "output", None)
    if isinstance(output, str):
        return output
    if isinstance(response, Mapping):
        value = response.get("output")
        if isinstance(value, str):
            return value
    return ""


def _discovery_command(work_dir: str) -> str:
    root = shlex.quote(work_dir)
    return (
        f"for d in {root} {root}/*; do "
        '[ -d "$d/.git" ] || continue; '
        'git -C "$d" remote get-url origin 2>/dev/null || true; '
        "done"
    )


async def discover_working_repo(backend: Any) -> tuple[str, str] | None:
    try:
        work_dir = await aresolve_sandbox_work_dir(backend)
        response = await backend.aexecute(
            _discovery_command(work_dir), timeout=_DISCOVER_TIMEOUT_SECONDS
        )
    except Exception:
        logger.debug("Failed to inspect the sandbox working repository", exc_info=True)
        return None

    repos = {
        repo
        for line in _output(response).splitlines()
        if (repo := github_repo_from_remote(line)) is not None
    }
    return next(iter(repos)) if len(repos) == 1 else None


class WorkingRepoMiddleware(AgentMiddleware):
    state_schema = AgentState

    def __init__(self, *, thread_id: str, backend: Any, thread_client: Any) -> None:
        self._thread_id = thread_id
        self._backend = backend
        self._thread_client = thread_client
        self._current_repo: tuple[str, str] | None = None

    async def _sync_repo(self) -> None:
        repo = await discover_working_repo(self._backend)
        if repo is None or repo == self._current_repo:
            return
        owner, name = repo
        try:
            await self._thread_client.threads.update(
                thread_id=self._thread_id,
                metadata={"working_repo_full_name": f"{owner}/{name}"},
            )
        except Exception:
            logger.debug(
                "Failed to update working repository for %s", self._thread_id, exc_info=True
            )
            return
        self._current_repo = repo

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        if _tool_name(request) in {"execute", "task"}:
            await self._sync_repo()
        return result
