"""Surface sandbox filesystem resets on first occurrence.

Tracks paths the agent has successfully created or written to in the
current trace. When a subsequent tool result references one of those
paths with a "No such file or directory" or "fatal: not a git
repository" error, emits a structured ``sandbox_reset_detected`` event
and prepends a user-facing notice to the next ``slack_thread_reply`` so
the infra failure becomes visible instead of being silently masked by
``ToolErrorMiddleware``'s re-clone path.
"""

from __future__ import annotations

import logging
import re
import shlex
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

SANDBOX_RESET_DETECTED_MARKER = "sandbox_reset_detected"
SANDBOX_RESET_USER_NOTICE = "Sandbox state was reset; re-doing earlier setup steps."

_RESET_ERROR_HINTS: tuple[str, ...] = (
    "No such file or directory",
    "fatal: not a git repository",
)

_PATH_ARG_KEYS: tuple[str, ...] = (
    "file_path",
    "path",
    "target_file",
    "target_path",
    "dest",
    "destination",
    "directory",
)


def _thread_id_from_request(request: ToolCallRequest) -> str | None:
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


def _tool_call_dict(request: ToolCallRequest) -> Mapping[str, Any]:
    tool_call = getattr(request, "tool_call", None)
    return tool_call if isinstance(tool_call, Mapping) else {}


def _tool_name(request: ToolCallRequest) -> str | None:
    name = _tool_call_dict(request).get("name")
    return name if isinstance(name, str) and name else None


def _tool_args(request: ToolCallRequest) -> dict[str, Any]:
    args = _tool_call_dict(request).get("args")
    return dict(args) if isinstance(args, Mapping) else {}


def _result_text(result: ToolMessage | Command) -> str:
    if not isinstance(result, ToolMessage):
        return ""
    content = result.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, Mapping):
                text = block.get("text", "")
                parts.append(text if isinstance(text, str) else str(text))
            else:
                parts.append(str(block))
        return " ".join(parts)
    return str(content)


def _looks_absolute_path(token: str) -> bool:
    return (
        token.startswith("/")
        or token.startswith("~")
        or re.match(r"^[a-zA-Z]:[\\/]", token) is not None
    )


def _extract_paths_from_execute(command: str) -> list[str]:
    """Best-effort extraction of paths from a shell command."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    paths: list[str] = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok in {"-C", "--git-dir", "--work-tree"}:
            skip_next = True
            continue
        if _looks_absolute_path(tok):
            paths.append(tok)
    if tokens and tokens[0] in {"mkdir", "git"} and "clone" in tokens:
        for tok in reversed(tokens):
            if _looks_absolute_path(tok):
                if tok not in paths:
                    paths.append(tok)
                break
    return paths


def _extract_written_paths(tool_name: str | None, args: Mapping[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in _PATH_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value:
            paths.append(value)
    if tool_name == "execute":
        command = args.get("command")
        if isinstance(command, str) and command:
            paths.extend(_extract_paths_from_execute(command))
    deduped: list[str] = []
    seen: set[str] = set()
    for p in paths:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


def _result_is_success(result: ToolMessage | Command) -> bool:
    if not isinstance(result, ToolMessage):
        return False
    status = getattr(result, "status", None)
    if status == "error":
        return False
    text = _result_text(result)
    if any(hint in text for hint in _RESET_ERROR_HINTS):
        return False
    return True


def _find_tracked_path_in_text(text: str, tracked: set[str]) -> str | None:
    for path in tracked:
        if path and path in text:
            return path
    return None


def _append_marker(result: ToolMessage, path: str) -> None:
    marker = f"[{SANDBOX_RESET_DETECTED_MARKER} path={path}]"
    content = result.content
    if isinstance(content, str):
        result.content = content + "\n" + marker if content else marker
    elif isinstance(content, list):
        result.content = [*content, {"type": "text", "text": marker}]
    else:
        result.content = f"{content}\n{marker}"


_NOTICES_PENDING: dict[str, bool] = {}
_TRACKED_PATHS: dict[str, set[str]] = {}


def _tracked_for(thread_id: str) -> set[str]:
    return _TRACKED_PATHS.setdefault(thread_id, set())


def _consume_pending_notice(thread_id: str) -> bool:
    return _NOTICES_PENDING.pop(thread_id, False)


def _queue_notice(thread_id: str) -> None:
    _NOTICES_PENDING[thread_id] = True


def _maybe_prepend_slack_notice(request: ToolCallRequest, thread_id: str) -> ToolCallRequest:
    if _tool_name(request) != "slack_thread_reply":
        return request
    if not _consume_pending_notice(thread_id):
        return request
    tool_call = _tool_call_dict(request)
    args = dict(tool_call.get("args") or {})
    text = args.get("text")
    if not isinstance(text, str):
        text = ""
    args["text"] = f"{SANDBOX_RESET_USER_NOTICE}\n\n{text}" if text else SANDBOX_RESET_USER_NOTICE
    new_tool_call = {**tool_call, "args": args}
    return request.override(tool_call=new_tool_call)


def _handle_result(
    request: ToolCallRequest,
    result: ToolMessage | Command,
    thread_id: str | None,
) -> ToolMessage | Command:
    if thread_id is None or not isinstance(result, ToolMessage):
        return result

    tracked = _tracked_for(thread_id)
    text = _result_text(result)

    if any(hint in text for hint in _RESET_ERROR_HINTS):
        path = _find_tracked_path_in_text(text, tracked)
        if path is None:
            tool_args = _tool_args(request)
            for p in _extract_written_paths(_tool_name(request), tool_args):
                if p in tracked:
                    path = p
                    break
        if path is not None:
            logger.warning(
                "Sandbox reset detected for tracked path",
                extra={
                    "event": SANDBOX_RESET_DETECTED_MARKER,
                    "path": path,
                    "thread_id": thread_id,
                },
            )
            _append_marker(result, path)
            _queue_notice(thread_id)
        return result

    if _result_is_success(result):
        for path in _extract_written_paths(_tool_name(request), _tool_args(request)):
            tracked.add(path)
    return result


class SandboxResetDetectorMiddleware(AgentMiddleware):
    """Detect sandbox filesystem resets and surface them immediately."""

    state_schema = AgentState

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        thread_id = _thread_id_from_request(request)
        if thread_id is not None:
            request = _maybe_prepend_slack_notice(request, thread_id)
        result = handler(request)
        return _handle_result(request, result, thread_id)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        thread_id = _thread_id_from_request(request)
        if thread_id is not None:
            request = _maybe_prepend_slack_notice(request, thread_id)
        result = await handler(request)
        return _handle_result(request, result, thread_id)
