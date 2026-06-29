"""Stamp file-edit tool results with a presentation diff (``ToolMessage.artifact``).

``edit_file`` / ``write_file`` return only a one-line summary, but the dashboard
renders a full-file diff per edit. This middleware reads the file's *before*
content from the sandbox once, computes the *after* content locally (write → the
new content from args; edit → applying the old→new replacement), and stamps the
result's ``artifact`` with a ``{"diff": {...}}`` payload.

``ToolMessage.artifact`` is a standard serialized field, so it survives the
checkpoint + ``GET …/state`` hydration: the client renders the same diff live
and on reload straight from ``stream.messages`` — no second adapter and no
client-side sandbox access (see ``ui/src/lib/agents/streamMessagesToUi.ts``).

Everything here is best-effort. On any failure — no cached sandbox, a binary or
truncated read, a missing ``old_string`` — the tool result is returned untouched
and the client falls back to deriving a fragment diff from the tool args.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from ..utils.sandbox_state import SANDBOX_BACKENDS

logger = logging.getLogger(__name__)

_EDIT_FILE = "edit_file"
_WRITE_FILE = "write_file"
_DIFF_TOOLS = frozenset({_EDIT_FILE, _WRITE_FILE})

# Cap the before-read: rendering a diff isn't worth pulling a huge file into
# memory, and a read at the cap is assumed truncated (skip → args fallback).
_MAX_DIFF_LINES = 20_000

_NOT_FOUND_HINTS = ("not found", "no such file", "does not exist", "file_not_found", "enoent")

# Prepended to the tool result content when an edit_file/write_file call did
# not change the file's bytes. Surfacing this in the model-visible content is
# what stops agents from re-applying the same edit in a tight loop.
_NOOP_NOTICE_PREFIX = (
    "[NO-OP: file content unchanged by this call — do NOT retry the same edit. "
    "Re-read the file to confirm its current state, try a structurally different "
    "edit, or surface the blocker and stop.]\n\n"
)


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
    raw = args.get("file_path") or args.get("path") or args.get("target_file")
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _classify_read(result: Any) -> tuple[str | None, str | None]:
    """Normalize a backend ``ReadResult`` to ``(content, error_kind)``.

    ``error_kind`` is ``None`` on success, ``"not_found"`` when the file is
    absent (a clean signal it's a new file), or ``"other"`` for anything we
    can't safely turn into a full-file diff (binary, truncated, unreadable).
    """
    error = getattr(result, "error", None)
    if error:
        text = str(error).lower()
        return None, "not_found" if any(h in text for h in _NOT_FOUND_HINTS) else "other"

    file_data = getattr(result, "file_data", None)
    if file_data is None:
        return None, "other"

    if isinstance(file_data, Mapping):
        encoding = file_data.get("encoding")
        content = file_data.get("content")
    else:
        encoding = getattr(file_data, "encoding", None)
        content = getattr(file_data, "content", None)

    if encoding is not None and encoding != "utf-8":
        return None, "other"  # base64 / binary
    if not isinstance(content, str):
        return None, "other"
    if content.count("\n") + 1 >= _MAX_DIFF_LINES:
        return None, "other"  # assume truncated at the read cap
    return content, None


def _build_diff_artifact(
    tool_name: str,
    args: Mapping[str, Any],
    before: str | None,
    before_kind: str | None,
) -> dict[str, Any] | None:
    """Pure: build the ``{"diff": {...}, "changed": bool}`` artifact, or ``None`` to skip."""
    file_path = _file_path(args)
    if file_path is None:
        return None

    if tool_name == _WRITE_FILE:
        new_content = args.get("content")
        if not isinstance(new_content, str):
            return None
        if before_kind is None and before is not None:
            return _diff(file_path, before, new_content, is_new=False)
        if before_kind == "not_found":
            return _diff(file_path, None, new_content, is_new=True)
        return None  # unknown prior state — let the client derive a fragment

    if tool_name == _EDIT_FILE:
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        if not isinstance(old_string, str) or not isinstance(new_string, str):
            return None
        # A full-file diff needs the real file. Requiring the edited span to be
        # present confirms the read is the genuine content (and not a binary,
        # truncated, or empty-placeholder read) before we trust it.
        if before is None or old_string not in before:
            return None
        if args.get("replace_all"):
            new_content = before.replace(old_string, new_string)
        else:
            new_content = before.replace(old_string, new_string, 1)
        return _diff(file_path, before, new_content, is_new=False)

    return None


def _diff(
    file_path: str, original: str | None, new_content: str, *, is_new: bool
) -> dict[str, Any]:
    return {
        "diff": {
            "filePath": file_path,
            "originalContent": original,
            "newContent": new_content,
            "isNewFile": is_new,
        },
        "changed": original != new_content,
    }


def _stamp(result: ToolMessage | Command, artifact: dict[str, Any] | None) -> None:
    if artifact is None or not isinstance(result, ToolMessage) or result.status == "error":
        return
    existing = result.artifact if isinstance(result.artifact, Mapping) else None
    result.artifact = {**existing, **artifact} if existing else artifact
    # Surface no-op edits in the model-visible content so the agent doesn't
    # treat an identical re-edit as fresh progress and loop on it.
    if artifact.get("changed") is False and isinstance(result.content, str):
        if not result.content.startswith(_NOOP_NOTICE_PREFIX):
            result.content = _NOOP_NOTICE_PREFIX + result.content


class ToolArtifactMiddleware(AgentMiddleware):
    """Attach a full-file diff to ``edit_file`` / ``write_file`` results.

    Runs inside ``ToolErrorMiddleware`` (added right after it in the stack) so
    error normalization still brackets the real tool call. Reads the file's
    pre-edit content once per edit; on any failure it no-ops.
    """

    state_schema = AgentState

    def _backend(self, request: ToolCallRequest) -> Any | None:
        thread_id = _thread_id(request)
        if not thread_id:
            return None
        return SANDBOX_BACKENDS.get(thread_id)

    def _read_before_sync(self, request: ToolCallRequest) -> tuple[str | None, str | None]:
        backend = self._backend(request)
        file_path = _file_path(_tool_args(request))
        if backend is None or file_path is None:
            return None, "other"
        try:
            result = backend.read(file_path, offset=0, limit=_MAX_DIFF_LINES)
        except Exception:
            logger.debug("tool_artifact: read failed for %s", file_path, exc_info=True)
            return None, "other"
        return _classify_read(result)

    async def _read_before_async(self, request: ToolCallRequest) -> tuple[str | None, str | None]:
        backend = self._backend(request)
        file_path = _file_path(_tool_args(request))
        if backend is None or file_path is None:
            return None, "other"
        try:
            result = await backend.aread(file_path, offset=0, limit=_MAX_DIFF_LINES)
        except Exception:
            logger.debug("tool_artifact: aread failed for %s", file_path, exc_info=True)
            return None, "other"
        return _classify_read(result)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        name = _tool_name(request)
        if name not in _DIFF_TOOLS:
            return handler(request)
        before, kind = self._read_before_sync(request)
        result = handler(request)
        _stamp(result, _build_diff_artifact(name, _tool_args(request), before, kind))
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        name = _tool_name(request)
        if name not in _DIFF_TOOLS:
            return await handler(request)
        before, kind = await self._read_before_async(request)
        result = await handler(request)
        _stamp(result, _build_diff_artifact(name, _tool_args(request), before, kind))
        return result
