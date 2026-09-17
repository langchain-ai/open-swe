"""Transcript view of a thread state: what the dashboard paints on first load.

The SDK hydrates the transcript from ``GET /threads/{id}/state``. The full
checkpoint is dominated by content the first frame never shows (tool results,
reasoning, provider metadata), so this module reduces a state payload to the
fields the transcript reads, and builds such a payload from the thread row's
``values`` so an idle thread never needs the checkpointer at all.
"""

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict

JsonValue = object
JsonObject = Mapping[str, JsonValue]

StateView = Literal["full", "transcript"]

TRIMMED_MARKER = "open_swe_trimmed"


class TranscriptStats(TypedDict):
    messages: int
    trimmed_tool_results: int


def _as_object(value: JsonValue) -> dict[str, JsonValue] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _text_only_content(content: JsonValue) -> JsonValue:
    """Keep string content as is; from block lists keep only text blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, Sequence):
        return ""
    kept: list[JsonValue] = []
    for block in content:
        if isinstance(block, Mapping) and block.get("type") == "text":
            kept.append(block)
    if not kept:
        return ""
    return kept


def _trim_human(message: JsonObject) -> dict[str, JsonValue]:
    trimmed: dict[str, JsonValue] = {
        "type": "human",
        "id": message.get("id"),
        "content": message.get("content"),
    }
    if message.get("name") is not None:
        trimmed["name"] = message["name"]
    extra = _as_object(message.get("additional_kwargs"))
    if extra:
        trimmed["additional_kwargs"] = extra
    return trimmed


def _trim_ai(message: JsonObject) -> dict[str, JsonValue]:
    trimmed: dict[str, JsonValue] = {
        "type": "ai",
        "id": message.get("id"),
        "content": _text_only_content(message.get("content")),
        "tool_calls": message.get("tool_calls") or [],
    }
    if message.get("name") is not None:
        trimmed["name"] = message["name"]
    response_metadata = _as_object(message.get("response_metadata"))
    if response_metadata and "created_at" in response_metadata:
        trimmed["response_metadata"] = {"created_at": response_metadata["created_at"]}
    extra = _as_object(message.get("additional_kwargs"))
    if extra and "lc_source" in extra:
        trimmed["additional_kwargs"] = {"lc_source": extra["lc_source"]}
    return trimmed


def _trim_tool(message: JsonObject) -> dict[str, JsonValue]:
    trimmed: dict[str, JsonValue] = {
        "type": "tool",
        "id": message.get("id"),
        "name": message.get("name"),
        "tool_call_id": message.get("tool_call_id"),
        "status": message.get("status", "success"),
        "content": "",
        "additional_kwargs": {TRIMMED_MARKER: True},
    }
    artifact = _as_object(message.get("artifact"))
    if artifact and "className" in artifact:
        trimmed["artifact"] = {"className": artifact["className"]}
    return trimmed


def trim_message(message: JsonValue) -> dict[str, JsonValue] | None:
    """Reduce one serialized LangChain message to the transcript's fields."""
    record = _as_object(message)
    if record is None:
        return None
    kind = record.get("type")
    if kind == "human":
        return _trim_human(record)
    if kind == "ai":
        return _trim_ai(record)
    if kind == "tool":
        return _trim_tool(record)
    return None


def trim_messages(
    messages: JsonValue,
    *,
    limit: int | None = None,
) -> tuple[list[dict[str, JsonValue]], TranscriptStats]:
    """Transcript projection of ``values["messages"]``, optionally the last ``limit``."""
    if not isinstance(messages, Sequence) or isinstance(messages, str):
        return [], {"messages": 0, "trimmed_tool_results": 0}
    kept: list[dict[str, JsonValue]] = []
    trimmed_tool_results = 0
    for message in messages:
        trimmed = trim_message(message)
        if trimmed is None:
            continue
        if trimmed["type"] == "tool":
            trimmed_tool_results += 1
        kept.append(trimmed)
    return kept, {"messages": len(kept), "trimmed_tool_results": trimmed_tool_results}


def transcript_values(
    values: JsonValue,
) -> tuple[dict[str, JsonValue], TranscriptStats]:
    """``values`` with ``messages`` replaced by their transcript projection."""
    record = _as_object(values) or {}
    messages, stats = trim_messages(record.get("messages"))
    record["messages"] = messages
    return record, stats


def state_from_thread_row(thread: JsonObject) -> dict[str, JsonValue]:
    """A ``ThreadState``-shaped payload from a thread row.

    The row's ``values`` are the latest checkpoint's values as of the last run
    end, which is exactly ``get_state().values`` while the thread is idle. The
    checkpoint envelope is unknown here; the SDK treats a missing checkpoint as
    a seed without fork/edit anchors, which the dashboard does not use.
    """
    values = _as_object(thread.get("values")) or {}
    metadata = _as_object(thread.get("metadata")) or {}
    interrupts = _as_object(thread.get("interrupts")) or {}
    tasks: list[JsonValue] = [
        {
            "id": task_id,
            "name": "",
            "path": [],
            "error": None,
            "interrupts": pending,
            "checkpoint": None,
            "state": None,
            "result": None,
        }
        for task_id, pending in interrupts.items()
        if isinstance(pending, list) and pending
    ]
    return {
        "values": values,
        "next": [],
        "tasks": tasks,
        "metadata": {key: metadata[key] for key in ("graph_id", "assistant_id") if key in metadata},
        "created_at": thread.get("state_updated_at") or thread.get("updated_at"),
        "checkpoint": None,
        "parent_checkpoint": None,
        "interrupts": [
            item for pending in interrupts.values() if isinstance(pending, list) for item in pending
        ],
        "checkpoint_id": None,
        "parent_checkpoint_id": None,
    }


def tool_results(
    values: JsonValue, tool_call_ids: Sequence[str]
) -> dict[str, dict[str, JsonValue]]:
    """Full tool messages for the given ``tool_call_id``s, keyed by id."""
    record = _as_object(values) or {}
    messages = record.get("messages")
    wanted = set(tool_call_ids)
    found: dict[str, dict[str, JsonValue]] = {}
    if not isinstance(messages, Sequence) or isinstance(messages, str):
        return found
    for message in messages:
        entry = _as_object(message)
        if entry is None or entry.get("type") != "tool":
            continue
        call_id = entry.get("tool_call_id")
        if isinstance(call_id, str) and call_id in wanted:
            found[call_id] = {
                "content": entry.get("content"),
                "artifact": entry.get("artifact"),
                "status": entry.get("status", "success"),
            }
    return found
