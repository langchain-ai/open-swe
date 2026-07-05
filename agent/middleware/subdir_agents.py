"""Auto-load applicable ``AGENTS.md`` files after file reads."""

from __future__ import annotations

import logging
import posixpath
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from ..utils.sandbox_state import SANDBOX_BACKENDS

logger = logging.getLogger(__name__)

_READ_FILE = "read_file"
_AGENTS_MD = "AGENTS.md"
_MAX_AGENTS_LINES = 1_000
_MAX_AGENTS_BYTES = 64 * 1024


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


def _thread_id(request: ToolCallRequest) -> str | None:
    runtime_config = getattr(getattr(request, "runtime", None), "config", None)
    config: Mapping[str, Any] | None = (
        runtime_config if isinstance(runtime_config, Mapping) else None
    )
    if config is None:
        try:
            maybe_config = get_config()
        except Exception:
            return None
        config = maybe_config if isinstance(maybe_config, Mapping) else None
    if config is None:
        return None
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        return None
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


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
        return None

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
    if not isinstance(content, str) or not content.strip():
        return None

    encoded = content.encode("utf-8")
    if len(encoded) <= _MAX_AGENTS_BYTES:
        return content
    return encoded[:_MAX_AGENTS_BYTES].decode("utf-8", errors="ignore") + "\n\n[truncated]"


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


def _append_reminder(result: ToolMessage | Command, reminder: str | None) -> ToolMessage | Command:
    if reminder is not None and _can_append_reminder(result):
        result.content = f"{result.content}\n\n{reminder}"
    return result


class SubdirAgentsReadMiddleware(AgentMiddleware):
    """Append applicable ancestor ``AGENTS.md`` files to ``read_file`` results."""

    state_schema = AgentState

    def __init__(self) -> None:
        self._loaded: defaultdict[str, set[str]] = defaultdict(set)

    def _thread_key(self, request: ToolCallRequest) -> str:
        return _thread_id(request) or "__unknown_thread__"

    def _backend(self, request: ToolCallRequest) -> Any | None:
        thread_id = _thread_id(request)
        if not thread_id:
            return None
        return SANDBOX_BACKENDS.get(thread_id)

    def _mark_direct_agents_read(self, request: ToolCallRequest, file_path: str) -> bool:
        if posixpath.basename(file_path) != _AGENTS_MD:
            return False
        self._loaded[self._thread_key(request)].add(file_path)
        return True

    def _load_sync(self, request: ToolCallRequest, file_path: str) -> str | None:
        if self._mark_direct_agents_read(request, file_path):
            return None
        backend = self._backend(request)
        if backend is None:
            return None
        loaded_paths = self._loaded[self._thread_key(request)]
        loaded: list[tuple[str, str]] = []
        for path in _candidate_agents_paths(file_path):
            if path in loaded_paths:
                continue
            loaded_paths.add(path)
            try:
                text = _extract_text(backend.read(path, offset=0, limit=_MAX_AGENTS_LINES))
            except Exception:
                logger.debug("subdir_agents: read failed for %s", path, exc_info=True)
                continue
            if text is None:
                continue
            loaded.append((path, text))
        return _system_reminder(file_path, loaded) if loaded else None

    async def _load_async(self, request: ToolCallRequest, file_path: str) -> str | None:
        if self._mark_direct_agents_read(request, file_path):
            return None
        backend = self._backend(request)
        if backend is None:
            return None
        loaded_paths = self._loaded[self._thread_key(request)]
        loaded: list[tuple[str, str]] = []
        for path in _candidate_agents_paths(file_path):
            if path in loaded_paths:
                continue
            loaded_paths.add(path)
            try:
                text = _extract_text(await backend.aread(path, offset=0, limit=_MAX_AGENTS_LINES))
            except Exception:
                logger.debug("subdir_agents: aread failed for %s", path, exc_info=True)
                continue
            if text is None:
                continue
            loaded.append((path, text))
        return _system_reminder(file_path, loaded) if loaded else None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        if _tool_name(request) != _READ_FILE:
            return handler(request)
        result = handler(request)
        file_path = _file_path(_tool_args(request))
        if file_path is None or not _can_append_reminder(result):
            return result
        return _append_reminder(result, self._load_sync(request, file_path))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if _tool_name(request) != _READ_FILE:
            return await handler(request)
        result = await handler(request)
        file_path = _file_path(_tool_args(request))
        if file_path is None or not _can_append_reminder(result):
            return result
        return _append_reminder(result, await self._load_async(request, file_path))
