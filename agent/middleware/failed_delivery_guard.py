"""After-agent middleware that escalates a silently-failed final delivery.

Per AGENTS.md there is intentionally no after-agent safety net, so this guard
is scoped narrowly: it fires only when the LAST tool call was a delivery tool
(``slack_thread_reply`` / ``linear_comment``) whose result reported
``success: false`` with an error the agent must not simply retry
(``not_in_channel``, ``channel_not_found``, ``missing_slack_bot_token``). In
that case the developer never received the completed work's outcome, so we
invoke ``report_platform_issue`` with the intended summary plus the returned
hint to surface it instead of exiting silently.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain.agents.middleware import AgentState, after_agent
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.runtime import Runtime

from ..tools.report_platform_issue import report_platform_issue

logger = logging.getLogger(__name__)

_DELIVERY_TOOLS = {"slack_thread_reply", "linear_comment"}
_ESCALATE_ERRORS = {"not_in_channel", "channel_not_found", "missing_slack_bot_token"}


def _as_dict(content: object) -> dict[str, Any] | None:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _tool_name_for_call(messages: list[Any], call_id: str | None) -> str | None:
    if not call_id:
        return None
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                if call.get("id") == call_id:
                    return call.get("name")
    return None


def _intended_summary(messages: list[Any], call_id: str | None) -> str | None:
    if not call_id:
        return None
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                if call.get("id") == call_id:
                    args = call.get("args") or {}
                    return args.get("message") or args.get("comment_body")
    return None


@after_agent
async def escalate_failed_final_delivery(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """Escalate via report_platform_issue when the final delivery silently failed."""
    messages = state.get("messages", [])
    if not messages:
        return None

    last = messages[-1]
    if not isinstance(last, ToolMessage):
        return None

    result = _as_dict(getattr(last, "content", None))
    if not result or result.get("success") is not False:
        return None
    if result.get("error") not in _ESCALATE_ERRORS:
        return None

    call_id = getattr(last, "tool_call_id", None)
    tool_name = getattr(last, "name", None) or _tool_name_for_call(messages, call_id)
    if tool_name not in _DELIVERY_TOOLS:
        return None

    summary = _intended_summary(messages, call_id)
    hint = result.get("hint")

    try:
        report = await report_platform_issue(summary=summary, hint=hint)
        logger.info(
            "Escalated failed final delivery (%s: %s) as report %s",
            tool_name,
            result.get("error"),
            report.get("report_id"),
        )
    except Exception:
        logger.exception("Failed to escalate silently-failed final delivery")
    return None
