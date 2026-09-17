"""Skeleton thread state: everything the transcript does not need for first paint, deferred.

The dashboard paints a transcript from user text, assistant text, tool call
names and arguments, statuses and timestamps. Tool results, artifacts,
reasoning text, pasted images, hidden summarization messages and non-message
state are the bulk of a long thread and only matter later: results and images
when a card is expanded or scrolled to, reasoning when its collapsed block is
opened. ``split_state`` separates the two: a skeleton carrying markers, and
the deferred parts the client fetches after first paint.
"""

import json
from collections.abc import Mapping
from typing import Any, Literal, TypedDict

from agent.utils.json_types import as_json_object

LAZY_MARKER_KEY = "open_swe_lazy"
SUMMARIZATION_SOURCE = "summarization"

DeferredPart = Literal["content", "artifact", "reasoning", "images"]
_IMAGE_BLOCK_TYPES = frozenset({"image", "image_url"})
_REASONING_BLOCK_TYPES = frozenset({"reasoning", "thinking"})
_TOOL_CALL_BLOCK_TYPES = frozenset({"tool_call", "tool_use"})


class LazyMarker(TypedDict):
    truncated: bool
    size: int
    parts: list[DeferredPart]


class DeferredToolResult(TypedDict):
    content: object
    artifact: object | None
    status: str | None


class DeferredParts(TypedDict):
    """What the skeleton left out, addressed the way the transcript looks things up."""

    tool_results: dict[str, DeferredToolResult]
    """By ``tool_call_id``."""
    reasoning: dict[str, str]
    """Reasoning text by assistant message id."""
    images: dict[str, list[object]]
    """Image content blocks by human message id, in content order."""


class SkeletonSummary(TypedDict):
    deferred: int
    """Messages that had something deferred."""
    deferred_bytes: int
    kept_bytes: int
    categories: dict[str, int]
    """Deferred bytes by category, for telemetry on real threads."""


def _size(value: object) -> int:
    try:
        return len(json.dumps(value))
    except TypeError, ValueError:
        return len(str(value))


def _blocks(content: object) -> list[object] | None:
    return content if isinstance(content, list) else None


def _block_type(block: object) -> str | None:
    if isinstance(block, Mapping):
        block_type = block.get("type")
        return block_type if isinstance(block_type, str) else None
    return None


def _is_image_block(block: object) -> bool:
    return _block_type(block) in _IMAGE_BLOCK_TYPES


def _reasoning_text(block: Mapping[str, Any]) -> str:
    for key in ("reasoning", "thinking", "text"):
        value = block.get(key)
        if isinstance(value, str):
            return value
    return ""


def _mark(message: Mapping[str, Any], size: int, parts: list[DeferredPart]) -> dict[str, Any]:
    marker: LazyMarker = {"truncated": True, "size": size, "parts": parts}
    additional = dict(as_json_object(message.get("additional_kwargs")))
    additional[LAZY_MARKER_KEY] = marker
    return additional


def _is_summarization(message: Mapping[str, Any]) -> bool:
    return as_json_object(message.get("additional_kwargs")).get("lc_source") == SUMMARIZATION_SOURCE


def _split_tool(
    message: dict[str, Any], parts: DeferredParts, categories: dict[str, int]
) -> dict[str, Any] | None:
    call_id = message.get("tool_call_id")
    if not isinstance(call_id, str):
        return None
    content = message.get("content")
    artifact = message.get("artifact")
    deferred: list[DeferredPart] = []
    size = 0
    if content not in (None, "", []):
        deferred.append("content")
        size += _size(content)
        categories["tool_content"] = categories.get("tool_content", 0) + _size(content)
    if artifact is not None:
        deferred.append("artifact")
        size += _size(artifact)
        categories["tool_artifact"] = categories.get("tool_artifact", 0) + _size(artifact)
    if not deferred:
        return None
    status = message.get("status")
    parts["tool_results"][call_id] = {
        "content": content,
        "artifact": artifact,
        "status": status if isinstance(status, str) else None,
    }
    copy = {key: value for key, value in message.items() if key != "artifact"}
    copy["content"] = ""
    copy["additional_kwargs"] = _mark(message, size, deferred)
    return copy


def _split_ai(
    message: dict[str, Any], parts: DeferredParts, categories: dict[str, int]
) -> dict[str, Any] | None:
    message_id = message.get("id")
    copy = dict(message)
    deferred: list[DeferredPart] = []
    size = 0
    if _is_summarization(message) and message.get("content") not in (None, "", []):
        # Hidden in the transcript; nothing of it is needed.
        size += _size(message.get("content"))
        categories["summarization"] = categories.get("summarization", 0) + size
        copy["content"] = ""
        deferred.append("content")
    elif isinstance(message_id, str):
        blocks = _blocks(message.get("content"))
        if blocks is not None:
            # `tool_calls` is what the SDK and the transcript read; the
            # `tool_call` content blocks repeat it (plus provider extras).
            drop_tool_call_blocks = bool(message.get("tool_calls"))
            reasoning: list[str] = []
            trimmed_blocks: list[object] = []
            changed = False
            for block in blocks:
                block_type = _block_type(block)
                if block_type in _REASONING_BLOCK_TYPES and isinstance(block, Mapping):
                    # Provider signatures ride in `extras` and can outweigh the
                    # text; the transcript only ever shows the text.
                    text = _reasoning_text(block)
                    if text:
                        reasoning.append(text)
                    size += _size(block)
                    trimmed_blocks.append({"type": "reasoning", "reasoning": ""})
                    changed = True
                    continue
                if drop_tool_call_blocks and block_type in _TOOL_CALL_BLOCK_TYPES:
                    categories["tool_call_blocks"] = categories.get("tool_call_blocks", 0) + _size(
                        block
                    )
                    changed = True
                    continue
                trimmed_blocks.append(block)
            if changed:
                copy["content"] = trimmed_blocks
            if size:
                categories["reasoning"] = categories.get("reasoning", 0) + size
            if reasoning:
                parts["reasoning"][message_id] = "".join(reasoning)
                deferred.append("reasoning")
    metadata = as_json_object(message.get("response_metadata"))
    if metadata:
        # Only the timestamp is read by the transcript.
        kept = {"created_at": metadata["created_at"]} if "created_at" in metadata else {}
        saved = _size(metadata) - _size(kept)
        if saved > 0:
            categories["response_metadata"] = categories.get("response_metadata", 0) + saved
            copy["response_metadata"] = kept
    if not deferred:
        return copy if copy != message else None
    copy["additional_kwargs"] = _mark(message, size, deferred)
    return copy


def _split_human(
    message: dict[str, Any], parts: DeferredParts, categories: dict[str, int]
) -> dict[str, Any] | None:
    message_id = message.get("id")
    blocks = _blocks(message.get("content"))
    if not isinstance(message_id, str) or blocks is None:
        return None
    images = [block for block in blocks if _is_image_block(block)]
    if not images:
        return None
    size = sum(_size(block) for block in images)
    categories["images"] = categories.get("images", 0) + size
    parts["images"][message_id] = images
    placeholder = {"type": "image", "mime_type": "", "data": ""}
    copy = dict(message)
    copy["content"] = [placeholder if _is_image_block(block) else block for block in blocks]
    copy["additional_kwargs"] = _mark(message, size, ["images"])
    return copy


def split_state(state: dict[str, Any]) -> tuple[dict[str, Any], DeferredParts, SkeletonSummary]:
    """Return ``(skeleton, deferred parts, summary)`` for a thread state.

    The skeleton keeps every message's id, type, timestamps, text, tool calls
    and status; a message that lost something carries
    ``additional_kwargs[LAZY_MARKER_KEY]`` naming the parts. ``values`` keeps
    only ``messages``: nothing else in it is read before first paint.
    """
    parts: DeferredParts = {"tool_results": {}, "reasoning": {}, "images": {}}
    categories: dict[str, int] = {}
    deferred_messages = 0
    values = as_json_object(state.get("values"))
    messages = values.get("messages")
    skeleton_messages: list[object] = []
    if isinstance(messages, list):
        for message in messages:
            replacement: dict[str, Any] | None = None
            if isinstance(message, dict):
                kind = message.get("type")
                if kind == "tool":
                    replacement = _split_tool(message, parts, categories)
                elif kind == "ai":
                    replacement = _split_ai(message, parts, categories)
                elif kind == "human":
                    replacement = _split_human(message, parts, categories)
            if replacement is not None and LAZY_MARKER_KEY in as_json_object(
                replacement.get("additional_kwargs")
            ):
                deferred_messages += 1
            skeleton_messages.append(replacement if replacement is not None else message)
    for key, value in values.items():
        if key != "messages":
            categories["values"] = categories.get("values", 0) + _size(value)
    skeleton = {**state, "values": {"messages": skeleton_messages}}
    summary: SkeletonSummary = {
        "deferred": deferred_messages,
        "deferred_bytes": sum(categories.values()),
        "kept_bytes": _size(skeleton),
        "categories": categories,
    }
    return skeleton, parts, summary
