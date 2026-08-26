"""Convert finalized LangGraph messages into the dashboard's durable UI shape."""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage


def _timestamp(message: object) -> str:
    response_metadata = (
        message.get("response_metadata")
        if isinstance(message, Mapping)
        else getattr(message, "response_metadata", None)
    )
    if isinstance(response_metadata, Mapping):
        for key in ("timestamp", "created_at"):
            value = response_metadata.get(key)
            if isinstance(value, str) and value:
                return value
    return datetime.now(UTC).isoformat()


def _author(message: object) -> str:
    if isinstance(message, HumanMessage):
        return "user"
    if isinstance(message, AIMessage):
        return "agent"
    if isinstance(message, ToolMessage):
        return "tool"
    if isinstance(message, SystemMessage):
        return "system"
    kind = (message.get("type") or message.get("role")) if isinstance(message, Mapping) else None
    return {
        "human": "user",
        "user": "user",
        "ai": "agent",
        "assistant": "agent",
        "tool": "tool",
        "system": "system",
    }.get(str(kind), "system")


def _chunks(content: object) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"kind": "text", "text": content}] if content else []
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes, bytearray)):
        return [{"kind": "text", "text": str(content)}] if content is not None else []
    chunks: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, str):
            chunks.append({"kind": "text", "text": block})
            continue
        if not isinstance(block, Mapping):
            continue
        kind = block.get("type")
        if kind in {"text", "text_delta"} and isinstance(block.get("text"), str):
            chunks.append({"kind": "text", "text": block["text"]})
        elif kind in {"reasoning", "thinking"}:
            text = block.get("reasoning") or block.get("thinking") or block.get("text")
            if isinstance(text, str):
                chunks.append({"kind": "reasoning", "text": text})
        elif kind in {"image", "image_url"}:
            data = block.get("base64")
            mime = block.get("mime_type") or block.get("mimeType")
            if isinstance(data, str) and isinstance(mime, str):
                chunks.append({"kind": "image", "base64": data, "mimeType": mime})
    return chunks


def message_to_ui(message: object) -> dict[str, Any] | None:
    if isinstance(message, Mapping):
        message_id = message.get("id")
        content = message.get("content")
    elif isinstance(message, BaseMessage):
        message_id = message.id
        content = message.content
    else:
        return None
    chunks = _chunks(content)
    tool_calls = (
        message.get("tool_calls")
        if isinstance(message, Mapping)
        else getattr(message, "tool_calls", None)
    )
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if not isinstance(call, Mapping):
                continue
            name = call.get("name")
            call_id = call.get("id")
            chunks.append(
                {
                    "kind": "tool-execution",
                    "toolCallId": str(call_id or "tool"),
                    "title": str(name or "Tool"),
                    "toolKind": "other",
                    "input": call.get("args") if isinstance(call.get("args"), Mapping) else {},
                    "status": "completed",
                }
            )
    author = _author(message)
    return {
        "id": str(message_id or f"{author}-{id(message)}"),
        "author": author,
        "timestamp": _timestamp(message),
        "chunks": chunks,
    }


def messages_to_ui(messages: object) -> list[dict[str, Any]]:
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes, bytearray)):
        return []
    return [converted for message in messages if (converted := message_to_ui(message)) is not None]


def ui_messages_to_state(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seeded: list[dict[str, Any]] = []
    for message in messages:
        payload = message.get("payload") if isinstance(message.get("payload"), Mapping) else message
        author = payload.get("author")
        chunks = payload.get("chunks")
        if not isinstance(chunks, Sequence):
            continue
        text = "\n".join(
            str(chunk["text"])
            for chunk in chunks
            if isinstance(chunk, Mapping)
            and chunk.get("kind") in {"text", "reasoning", "code", "error"}
            and isinstance(chunk.get("text"), str)
        )
        if not text:
            continue
        kind = {"user": "human", "agent": "ai"}.get(str(author), "system")
        seeded.append(
            {
                "type": kind,
                "id": str(payload.get("id") or f"handoff-{len(seeded)}"),
                "content": text,
            }
        )
    if seeded:
        seeded.insert(
            0,
            {
                "type": "system",
                "id": "open-swe-handoff-context",
                "content": f"Resumed from another execution environment with {len(seeded)} prior messages.",
            },
        )
    return seeded
