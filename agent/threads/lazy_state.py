"""Skeleton thread state: tool results trimmed out of the hydration payload.

The dashboard paints a transcript from user messages, assistant text and tool
call names; tool *results* are the bulk of a long thread's state and only
matter once a card is expanded. ``skeletonize_state`` replaces each large tool
result with a short preview plus a marker, and ``deferred_tool_results`` returns
the full results for the client to fetch after first paint.
"""

import json
from collections.abc import Mapping
from typing import Any, TypedDict

from agent.utils.json_types import as_json_object

LAZY_MARKER_KEY = "open_swe_lazy"
PREVIEW_CHARS = 200
# Results smaller than this ride along in the skeleton: deferring them saves
# nothing and costs a placeholder.
DEFER_MIN_CHARS = 512


class LazyMarker(TypedDict):
    truncated: bool
    size: int


class DeferredToolResult(TypedDict):
    content: object
    artifact: object | None
    status: str | None


class SkeletonSummary(TypedDict):
    deferred: int
    deferred_bytes: int


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        if parts:
            return "\n".join(parts)
    try:
        return json.dumps(content)
    except TypeError, ValueError:
        return str(content)


def _payload_size(message: Mapping[str, Any]) -> int:
    try:
        return len(json.dumps(message.get("content"))) + len(json.dumps(message.get("artifact")))
    except TypeError, ValueError:
        return len(str(message.get("content"))) + len(str(message.get("artifact")))


def _tool_messages(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    messages = as_json_object(state.get("values")).get("messages")
    if not isinstance(messages, list):
        return []
    return [
        message
        for message in messages
        if isinstance(message, dict)
        and message.get("type") == "tool"
        and isinstance(message.get("tool_call_id"), str)
    ]


def _is_deferrable(message: Mapping[str, Any]) -> bool:
    return _payload_size(message) >= DEFER_MIN_CHARS


def skeletonize_state(state: dict[str, Any]) -> tuple[dict[str, Any], SkeletonSummary]:
    """Return a copy of ``state`` with large tool results replaced by previews.

    Each trimmed message keeps its id, ``tool_call_id``, ``status`` and name,
    gets ``content`` cut to :data:`PREVIEW_CHARS` characters, drops ``artifact``
    and carries ``additional_kwargs[LAZY_MARKER_KEY]`` so the client knows to
    fetch the full result. Everything else in the payload is untouched.
    """
    summary: SkeletonSummary = {"deferred": 0, "deferred_bytes": 0}
    values = as_json_object(state.get("values"))
    messages = values.get("messages")
    if not isinstance(messages, list):
        return state, summary
    trimmed: list[Any] = []
    for message in messages:
        if not (
            isinstance(message, dict)
            and message.get("type") == "tool"
            and isinstance(message.get("tool_call_id"), str)
            and _is_deferrable(message)
        ):
            trimmed.append(message)
            continue
        size = _payload_size(message)
        marker: LazyMarker = {"truncated": True, "size": size}
        additional = dict(as_json_object(message.get("additional_kwargs")))
        additional[LAZY_MARKER_KEY] = marker
        preview = _content_text(message.get("content"))[:PREVIEW_CHARS]
        copy = {key: value for key, value in message.items() if key != "artifact"}
        copy["content"] = preview
        copy["additional_kwargs"] = additional
        trimmed.append(copy)
        summary["deferred"] += 1
        summary["deferred_bytes"] += size
    if summary["deferred"] == 0:
        return state, summary
    return {**state, "values": {**values, "messages": trimmed}}, summary


def deferred_tool_results(state: Mapping[str, Any]) -> dict[str, DeferredToolResult]:
    """The full results ``skeletonize_state`` would trim, keyed by ``tool_call_id``."""
    results: dict[str, DeferredToolResult] = {}
    for message in _tool_messages(state):
        if not _is_deferrable(message):
            continue
        status = message.get("status")
        results[message["tool_call_id"]] = {
            "content": message.get("content"),
            "artifact": message.get("artifact"),
            "status": status if isinstance(status, str) else None,
        }
    return results
