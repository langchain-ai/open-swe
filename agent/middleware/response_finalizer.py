"""After-agent middleware that ensures the final assistant message is non-empty."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import AgentState, after_agent
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

_LIMIT_MARKER = "Model call limits exceeded"
_MAX_RESULT_PREVIEW = 200
_MAX_ARGS_PREVIEW = 80


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: list[str] = []
    for block in content:
        if isinstance(block, Mapping):
            text = block.get("text", "")
            parts.append(text if isinstance(text, str) else str(text))
        else:
            parts.append(str(block))
    return " ".join(parts).strip()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _summarize_args(args: object) -> str:
    if not args:
        return ""
    if isinstance(args, Mapping):
        rendered = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:3])
    else:
        rendered = str(args)
    return _truncate(rendered, _MAX_ARGS_PREVIEW)


def _last_tool_action(messages: list[Any]) -> tuple[str, str, str] | None:
    """Return (tool_name, args_summary, result_preview) from the most recent tool exchange."""
    last_tool_msg: ToolMessage | None = None
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage):
            last_tool_msg = msg
            break
    if last_tool_msg is None:
        return None

    tool_name = last_tool_msg.name or "tool"
    args_summary = ""
    tool_call_id = last_tool_msg.tool_call_id
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                if tc.get("id") == tool_call_id:
                    tool_name = tc.get("name") or tool_name
                    args_summary = _summarize_args(tc.get("args"))
                    break
            if args_summary or tool_name != (last_tool_msg.name or "tool"):
                break

    result_preview = _truncate(_content_to_text(last_tool_msg.content), _MAX_RESULT_PREVIEW)
    return tool_name, args_summary, result_preview


def _synthesize_summary(messages: list[Any]) -> str:
    action = _last_tool_action(messages)
    if action is None:
        return "Completed run with no further output."
    tool_name, args_summary, result_preview = action
    args_part = f"({args_summary})" if args_summary else "()"
    result_part = f"; result: {result_preview}" if result_preview else ""
    return f"Completed run: last action was {tool_name}{args_part}{result_part}."


def _step_limit_summary(messages: list[Any]) -> str:
    action = _last_tool_action(messages)
    if action is None:
        return "Reached step limit before finishing the task."
    tool_name, args_summary, _ = action
    args_part = f"({args_summary})" if args_summary else "()"
    return f"Reached step limit before finishing; last action was {tool_name}{args_part}."


@after_agent
def finalize_response(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:  # noqa: ARG001
    """Replace empty final assistant text with a deterministic completion summary."""
    messages = state.get("messages", [])
    if not messages:
        return None

    last_msg = messages[-1]
    if not isinstance(last_msg, AIMessage):
        return None

    text = _content_to_text(getattr(last_msg, "content", "") or "")
    if _LIMIT_MARKER in text:
        last_msg.content = _step_limit_summary(messages)
        return {"messages": [last_msg]}

    if text:
        return None

    if getattr(last_msg, "tool_calls", None):
        # A trailing tool call with no accompanying text means the run was cut
        # off mid-step (e.g. by the recursion limit) — synthesize an explicit
        # limit message so consumers don't see an empty completion.
        summary = _step_limit_summary(messages)
    else:
        summary = _synthesize_summary(messages)

    last_msg.content = summary
    last_msg.tool_calls = []
    return {"messages": [last_msg]}
