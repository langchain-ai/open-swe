"""A lighter projection of thread state for painting the transcript.

The dashboard shows every user and assistant message at the top level but
folds tool activity away, so the first paint needs the conversation's text,
not its tool outputs or pasted screenshots. The trimmed view keeps every
message and its id, blanks the large payloads, and marks each blank so the
client can fetch the full message on demand.
"""

import json
from typing import Literal, TypedDict

from agent.utils.json_types import JsonObject

type ThreadStateView = Literal["full", "trimmed"]

STUB_KEY = "open_swe_stub"
TOOL_OUTPUT_STUB_BYTES = 4096


class StubMarker(TypedDict):
    kind: Literal["tool_output", "image"]
    bytes: int


class TrimStats(TypedDict):
    stubbed: int
    saved_bytes: int


def _json_size(value: object) -> int:
    return len(json.dumps(value, separators=(",", ":"), default=str))


def _strip_inline_images(message: JsonObject) -> int:
    content = message.get("content")
    if not isinstance(content, list):
        return 0
    saved = 0
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue
        encoded = block.get("base64")
        if not isinstance(encoded, str) or not encoded:
            continue
        saved += len(encoded)
        del block["base64"]
        block[STUB_KEY] = StubMarker(kind="image", bytes=len(encoded))
    return saved


def _stub_tool_output(message: JsonObject) -> int:
    if message.get("type") != "tool":
        return 0
    size = _json_size(message.get("content"))
    artifact = message.get("artifact")
    if artifact is not None:
        size += _json_size(artifact)
    if size <= TOOL_OUTPUT_STUB_BYTES:
        return 0
    message["content"] = ""
    message.pop("artifact", None)
    kwargs = message.get("additional_kwargs")
    if not isinstance(kwargs, dict):
        kwargs = {}
        message["additional_kwargs"] = kwargs
    kwargs[STUB_KEY] = StubMarker(kind="tool_output", bytes=size)
    return size


def trim_thread_state(state: JsonObject) -> TrimStats:
    """Blank large tool outputs and inline images in ``state`` in place."""
    stats = TrimStats(stubbed=0, saved_bytes=0)
    values = state.get("values")
    messages = values.get("messages") if isinstance(values, dict) else None
    if not isinstance(messages, list):
        return stats
    for message in messages:
        if not isinstance(message, dict):
            continue
        saved = _stub_tool_output(message) + _strip_inline_images(message)
        if saved:
            stats["stubbed"] += 1
            stats["saved_bytes"] += saved
    return stats
