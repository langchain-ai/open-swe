"""Convert LangGraph / LangChain message dicts into dashboard UI message payloads."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from ..utils.messages import extract_text_content

_READ_TOOLS = frozenset({"read_file", "read", "glob", "grep"})
_EDIT_TOOLS = frozenset({"write_file", "edit_file", "str_replace", "write", "edit", "patch"})
_EXECUTE_TOOLS = frozenset({"execute", "bash", "shell", "run_terminal_cmd"})
_SEARCH_TOOLS = frozenset({"glob", "grep", "web_search", "fetch_url", "search"})
_INTERNAL_TOOLS = frozenset({"confirming_completion", "no_op"})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _message_type(message: dict[str, Any]) -> str:
    raw = message.get("type")
    if isinstance(raw, str):
        return raw.lower()
    role = message.get("role")
    if role == "user":
        return "human"
    if role == "assistant":
        return "ai"
    if role == "tool":
        return "tool"
    return "unknown"


def _tool_kind(name: str) -> str:
    lowered = name.lower()
    if lowered in _EDIT_TOOLS or any(token in lowered for token in ("edit", "write", "replace")):
        return "edit"
    if lowered in _EXECUTE_TOOLS:
        return "execute"
    if lowered in _SEARCH_TOOLS:
        return "search"
    if lowered in _READ_TOOLS or "read" in lowered:
        return "read"
    if lowered == "think":
        return "think"
    if lowered in {"fetch", "fetch_url", "http_request"}:
        return "fetch"
    return "other"


def _tool_title(name: str, args: dict[str, Any]) -> str:
    path = args.get("path") or args.get("file_path") or args.get("target_file")
    if isinstance(path, str) and path.strip():
        return f"{name} {path.strip()}"
    command = args.get("command")
    if isinstance(command, str) and command.strip():
        first = command.strip().splitlines()[0]
        return first[:120]
    return name.replace("_", " ").strip() or "Tool"


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}
        return parsed if isinstance(parsed, dict) else {"raw": raw}
    return {}


def _maybe_diff_from_args(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    path = args.get("path") or args.get("file_path") or args.get("target_file")
    if not isinstance(path, str) or not path.strip():
        return None
    old_content = args.get("old_string") or args.get("original_content")
    new_content = args.get("new_string") or args.get("content") or args.get("new_content")
    if not isinstance(new_content, str):
        return None
    original = old_content if isinstance(old_content, str) else None
    return {
        "originalContent": original,
        "newContent": new_content,
        "filePath": path.strip(),
        "isNewFile": original is None,
        "isBinary": False,
        "isTruncated": False,
        "totalLines": max(new_content.count("\n"), 0) + 1,
    }


def _is_internal_tool(name: str) -> bool:
    return name in _INTERNAL_TOOLS


def _append_agent_chunks(
    agent_turn: dict[str, Any] | None,
    *,
    msg_id: str,
    timestamp: str,
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    if agent_turn is None:
        return {
            "id": msg_id,
            "author": "agent",
            "timestamp": timestamp,
            "chunks": list(chunks),
        }
    agent_turn["timestamp"] = timestamp
    agent_turn["chunks"].extend(chunks)
    return agent_turn


def _merge_text_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the latest text chunk when middleware produced a follow-up AI message."""
    text_indices = [i for i, chunk in enumerate(chunks) if chunk.get("kind") == "text"]
    if len(text_indices) <= 1:
        return chunks
    last_text = text_indices[-1]
    return [
        chunk for i, chunk in enumerate(chunks) if chunk.get("kind") != "text" or i == last_text
    ]


def state_messages_to_ui(messages: list[Any]) -> list[dict[str, Any]]:
    """Map LangGraph state ``messages`` to the dashboard ``Message`` JSON shape."""
    pending_tools: dict[str, dict[str, Any]] = {}
    ui_messages: list[dict[str, Any]] = []
    agent_turn: dict[str, Any] | None = None

    for index, raw in enumerate(messages):
        if not isinstance(raw, dict):
            continue
        msg_type = _message_type(raw)
        msg_id = raw.get("id") if isinstance(raw.get("id"), str) else f"msg-{index}"
        timestamp = raw.get("created_at") if isinstance(raw.get("created_at"), str) else _now_iso()

        if msg_type in {"human", "user"}:
            if agent_turn is not None:
                agent_turn["chunks"] = _merge_text_chunks(agent_turn["chunks"])
                ui_messages.append(agent_turn)
                agent_turn = None
            text = extract_text_content(raw.get("content", ""))
            if not text:
                continue
            ui_messages.append(
                {
                    "id": msg_id,
                    "author": "user",
                    "timestamp": timestamp,
                    "chunks": [{"kind": "text", "text": text}],
                }
            )
            continue

        if msg_type in {"ai", "assistant"}:
            chunks: list[dict[str, Any]] = []
            text = extract_text_content(raw.get("content", ""))
            if text:
                chunks.append({"kind": "text", "text": text})

            for tool_call in raw.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                name = tool_call.get("name")
                if not isinstance(name, str) or not name:
                    name = "tool"
                if _is_internal_tool(name):
                    continue
                tool_call_id = tool_call.get("id") or tool_call.get("tool_call_id")
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    tool_call_id = f"tool-{uuid.uuid4().hex[:8]}"
                args = _parse_tool_args(tool_call.get("args"))
                chunk: dict[str, Any] = {
                    "kind": "tool-execution",
                    "toolCallId": tool_call_id,
                    "title": _tool_title(name, args),
                    "toolKind": _tool_kind(name),
                    "input": args,
                    "status": "in_progress",
                }
                diff_data = _maybe_diff_from_args(name, args)
                if diff_data:
                    chunk["diffData"] = diff_data
                chunks.append(chunk)
                pending_tools[tool_call_id] = chunk

            if chunks:
                agent_turn = _append_agent_chunks(
                    agent_turn, msg_id=msg_id, timestamp=timestamp, chunks=chunks
                )
            continue

        if msg_type == "tool":
            tool_call_id = raw.get("tool_call_id")
            if not isinstance(tool_call_id, str):
                continue
            name = raw.get("name") if isinstance(raw.get("name"), str) else "tool"
            if _is_internal_tool(name):
                pending_tools.pop(tool_call_id, None)
                continue
            chunk = pending_tools.get(tool_call_id)
            output = extract_text_content(raw.get("content", ""))
            if chunk is not None:
                chunk["status"] = "error" if raw.get("status") == "error" else "completed"
                if output:
                    chunk["output"] = output
                continue

            if agent_turn is None:
                agent_turn = {
                    "id": msg_id,
                    "author": "agent",
                    "timestamp": timestamp,
                    "chunks": [],
                }
            agent_turn["chunks"].append(
                {
                    "kind": "tool-execution",
                    "toolCallId": tool_call_id,
                    "title": name,
                    "toolKind": _tool_kind(name),
                    "status": "completed",
                    "output": output,
                }
            )

    if agent_turn is not None:
        agent_turn["chunks"] = _merge_text_chunks(agent_turn["chunks"])
        ui_messages.append(agent_turn)

    return ui_messages
