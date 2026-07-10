"""After-agent middleware guaranteeing a Slack-origin run ends with a reply.

A completed coding task is not "done" until the developer who triggered it has
been told in-thread. The agent sometimes treats a successful terminal GitHub
action (``git push``, ``open_pull_request``, ``gh pr edit``) as closeout and
ends the run without ever calling ``slack_thread_reply`` — leaving the developer
with no confirmation and no PR link. This hook inspects the trajectory at
end-of-run: for Slack-origin threads where no ``slack_thread_reply`` returned
``success: true``, it synthesizes a closeout from the final assistant message
plus the last successful PR action and posts it to the source thread.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import AgentState, after_agent
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..utils.slack import post_slack_thread_reply

logger = logging.getLogger(__name__)

_PR_ACTION_TOOLS = {"open_pull_request"}


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
    return " ".join(parts)


def _tool_result_payload(message: ToolMessage) -> Any:
    content = message.content
    if isinstance(content, (dict, list)):
        return content
    text = _content_to_text(content)
    if not text.strip():
        return text
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return text


def _tool_call_names(message: AIMessage) -> list[str]:
    names: list[str] = []
    for tool_call in getattr(message, "tool_calls", None) or []:
        name = tool_call.get("name") if isinstance(tool_call, dict) else None
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _slack_reply_succeeded(messages: list[Any]) -> bool:
    reply_call_ids = {
        tool_call.get("id")
        for message in messages
        if isinstance(message, AIMessage)
        for tool_call in getattr(message, "tool_calls", None) or []
        if isinstance(tool_call, dict) and tool_call.get("name") == "slack_thread_reply"
    }
    if not reply_call_ids:
        return False
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        if message.tool_call_id not in reply_call_ids:
            continue
        payload = _tool_result_payload(message)
        if isinstance(payload, Mapping) and payload.get("success") is True:
            return True
    return False


def _last_pr_url(messages: list[Any]) -> str | None:
    pr_call_ids = {
        tool_call.get("id"): tool_call.get("name")
        for message in messages
        if isinstance(message, AIMessage)
        for tool_call in getattr(message, "tool_calls", None) or []
        if isinstance(tool_call, dict) and tool_call.get("name") in _PR_ACTION_TOOLS
    }
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            continue
        if message.tool_call_id not in pr_call_ids:
            continue
        payload = _tool_result_payload(message)
        if isinstance(payload, Mapping) and payload.get("success") is True:
            url = payload.get("url")
            if isinstance(url, str) and url.strip():
                return url.strip()
    return None


def _last_assistant_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            text = _content_to_text(getattr(message, "content", "") or "").strip()
            if text:
                return text
    return ""


def _synthesize_closeout(messages: list[Any]) -> str | None:
    summary = _last_assistant_text(messages)
    pr_url = _last_pr_url(messages)
    if not summary and not pr_url:
        return None
    if pr_url and pr_url not in summary:
        summary = f"{summary}\n{pr_url}" if summary else pr_url
    return summary or None


@after_agent
async def ensure_slack_closeout_reply(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """Post a closeout reply if a Slack-origin run ended without one."""
    messages = state.get("messages", [])
    if not messages:
        return None

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    slack_thread = configurable.get("slack_thread") if isinstance(configurable, dict) else None
    if not isinstance(slack_thread, dict):
        return None

    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if (
        not isinstance(channel_id, str)
        or not isinstance(thread_ts, str)
        or not channel_id
        or not thread_ts
    ):
        return None

    if _slack_reply_succeeded(messages):
        return None

    message = _synthesize_closeout(messages)
    if not message:
        return None

    try:
        await post_slack_thread_reply(channel_id, thread_ts, message)
        logger.info("Posted synthesized closeout reply to Slack thread %s", thread_ts)
    except Exception:
        logger.exception("Failed to post synthesized closeout reply")

    return None
