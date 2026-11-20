"""Auto-load applicable ``AGENTS.md`` files after file reads, from the sandbox the read ran in."""

import asyncio
import logging
import posixpath
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from rt.loop import visible_messages
from rt.tracing import MIDDLEWARE_TRACE_POLICY

logger = logging.getLogger(__name__)

_READ_FILE = "read_file"
_AGENTS_MD = "AGENTS.md"
_MAX_AGENTS_LINES = 1_000
_MAX_AGENTS_BYTES = 64 * 1024
_DELIVERED_AGENTS = "robin_loaded_agents"
_ABSENT_AGENTS = "robin_absent_agents"
_MISSING_FILE = re.compile(r"(?::\s*file_not_found|^File '.*' not found)$")


def _tool_name(request: ToolCallRequest) -> str | None:
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, Mapping):
        name = tool_call.get("name")
        return name if isinstance(name, str) and name else None
    return None


def _tool_args(request: ToolCallRequest) -> dict[str, Any]:
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, Mapping):
        args = tool_call.get("args")
        if isinstance(args, Mapping):
            return dict(args)
    return {}


def _file_path(args: Mapping[str, Any]) -> str | None:
    raw = args.get("file_path")
    if not isinstance(raw, str):
        return None
    path = raw.strip()
    if not path.startswith("/"):
        return None
    return posixpath.normpath(path)


def _candidate_agents_paths(file_path: str) -> list[str]:
    current = posixpath.dirname(file_path)
    candidates: list[str] = []
    while current and current != "/":
        candidates.append(posixpath.join(current, _AGENTS_MD))
        parent = posixpath.dirname(current)
        if parent == current:
            break
        current = parent
    candidates.reverse()
    return [candidate for candidate in candidates if candidate != file_path]


def _extract_text(result: Any) -> str | None:
    error = getattr(result, "error", None)
    if error:
        return "" if isinstance(error, str) and _MISSING_FILE.search(error) else None

    file_data = getattr(result, "file_data", None)
    if file_data is None:
        return None

    if isinstance(file_data, Mapping):
        encoding = file_data.get("encoding")
        content = file_data.get("content")
    else:
        encoding = getattr(file_data, "encoding", None)
        content = getattr(file_data, "content", None)

    if encoding is not None and encoding != "utf-8":
        return None
    if not isinstance(content, str):
        return None
    if not content.strip():
        return ""

    encoded = content.encode("utf-8")
    if len(encoded) <= _MAX_AGENTS_BYTES:
        return content
    return encoded[:_MAX_AGENTS_BYTES].decode("utf-8", errors="ignore") + "\n\n[truncated]"


async def _read_agents_file(backend: BackendProtocol, path: str) -> str | None:
    """The file's instructions, ``""`` when there are none to give, or ``None`` when the read failed."""
    try:
        return _extract_text(await backend.aread(path, offset=0, limit=_MAX_AGENTS_LINES))
    except Exception:
        logger.debug("subdir_agents: aread failed for %s", path, exc_info=True)
        return None


def _system_reminder(file_path: str, loaded: Iterable[tuple[str, str]]) -> str:
    sections = [f"Instructions from: {path}\n{content.rstrip()}" for path, content in loaded]
    body = "\n\n".join(sections)
    return (
        "<system-reminder>\n"
        f"Loaded applicable AGENTS.md instructions for `{file_path}`. "
        "Follow these before editing files under their scopes; more deeply nested "
        "instructions take precedence.\n\n"
        f"{body}\n"
        "</system-reminder>"
    )


def _can_append_reminder(result: ToolMessage | Command) -> bool:
    return (
        isinstance(result, ToolMessage)
        and getattr(result, "status", None) != "error"
        and isinstance(result.content, str)
    )


class SubdirAgentsReadMiddleware(AgentMiddleware):
    """Append applicable ancestor ``AGENTS.md`` files to ``read_file`` results."""

    trace_policy = MIDDLEWARE_TRACE_POLICY

    state_schema = AgentState

    def backend(self, request: ToolCallRequest) -> BackendProtocol | None:
        """The thread's files: its sandbox, behind the run's backend."""
        backend = getattr(request.runtime, "backend", None)
        return backend if isinstance(backend, BackendProtocol) else None

    @staticmethod
    def _settled_paths(request: ToolCallRequest) -> set[str]:
        """Paths the visible conversation already delivered or found without instructions."""
        paths: set[str] = set()
        for message in visible_messages(request.state):
            if not isinstance(message, ToolMessage) or message.status == "error":
                continue
            for key in (_DELIVERED_AGENTS, _ABSENT_AGENTS):
                recorded = message.additional_kwargs.get(key)
                if isinstance(recorded, list):
                    paths.update(path for path in recorded if isinstance(path, str))
        return paths

    async def _load_async(
        self, request: ToolCallRequest, file_path: str
    ) -> tuple[list[tuple[str, str]], list[str]]:
        backend = self.backend(request)
        if backend is None:
            return [], []
        settled = self._settled_paths(request)
        paths = [path for path in _candidate_agents_paths(file_path) if path not in settled]
        texts = await asyncio.gather(*(_read_agents_file(backend, path) for path in paths))
        read = list(zip(paths, texts, strict=True))
        return [(path, text) for path, text in read if text], [p for p, t in read if t == ""]

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if _tool_name(request) != _READ_FILE:
            return await handler(request)
        result = await handler(request)
        file_path = _file_path(_tool_args(request))
        if (
            file_path is None
            or not isinstance(result, ToolMessage)
            or not _can_append_reminder(result)
        ):
            return result
        content = result.content
        if posixpath.basename(file_path) == _AGENTS_MD:
            recorded = {_DELIVERED_AGENTS: [file_path]}
        else:
            loaded, absent = await self._load_async(request, file_path)
            recorded = {
                key: paths
                for key, paths in (
                    (_DELIVERED_AGENTS, [path for path, _ in loaded]),
                    (_ABSENT_AGENTS, absent),
                )
                if paths
            }
            if not recorded:
                return result
            if loaded:
                content = f"{content}\n\n{_system_reminder(file_path, loaded)}"
        return result.model_copy(
            update={
                "content": content,
                "additional_kwargs": {**result.additional_kwargs, **recorded},
            }
        )
