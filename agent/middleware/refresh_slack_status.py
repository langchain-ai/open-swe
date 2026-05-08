"""Before-model middleware that refreshes the Slack typing indicator.

Slack's `assistants.threads.setStatus` indicator expires after ~2 minutes,
so on long agent runs it would silently disappear. Refreshing on every
model call keeps it visible while the agent is actively working — Slack
auto-clears it the moment the bot posts a real reply.

The status text is derived from the most recent assistant tool calls when
possible (e.g. "searching the codebase…" after a grep) so the indicator
reflects what the agent is actually doing. A curated list of rotating
loading messages is passed alongside as a fallback animation.

Gated behind `SLACK_ASSISTANTS_API_ENABLED` (checked inside
``set_slack_assistant_status``); a no-op when the flag is off or the run
has no associated Slack thread.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentState, before_model
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..utils.slack import (
    DEFAULT_ASSISTANT_STATUS,
    DEFAULT_LOADING_MESSAGES,
    set_slack_assistant_status,
)

logger = logging.getLogger(__name__)


# Tool-name → human-readable status. Keep in sync with the tool list in
# agent/server.py and the deepagents built-ins (read_file, write_file,
# edit_file, execute, glob, grep, task).
_TOOL_STATUS: dict[str, str] = {
    "read_file": "reading files…",
    "write_file": "editing files…",
    "edit_file": "editing files…",
    "execute": "running commands…",
    "glob": "scanning the repo…",
    "grep": "searching the codebase…",
    "task": "delegating to a subagent…",
    "web_search": "searching the web…",
    "fetch_url": "fetching a URL…",
    "http_request": "making an HTTP request…",
    "request_pr_review": "requesting a PR review…",
    "slack_read_thread_messages": "reading Slack history…",
    "slack_thread_reply": "drafting a Slack reply…",
    "linear_comment": "commenting on Linear…",
    "linear_create_issue": "creating a Linear issue…",
    "linear_get_issue": "checking Linear…",
    "linear_get_issue_comments": "checking Linear…",
    "linear_list_teams": "checking Linear…",
    "linear_update_issue": "updating Linear…",
    "linear_delete_issue": "updating Linear…",
}


def _status_from_recent_tool_calls(messages: list[Any]) -> str:
    """Pick a status string based on the last assistant message's tool calls."""
    for msg in reversed(messages):
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            continue
        # Use the first tool call's name; if the agent fans out, this is fine
        # as a single-line indicator.
        first = tool_calls[0]
        name = first.get("name") if isinstance(first, dict) else getattr(first, "name", None)
        if isinstance(name, str) and name in _TOOL_STATUS:
            return _TOOL_STATUS[name]
        return DEFAULT_ASSISTANT_STATUS
    return DEFAULT_ASSISTANT_STATUS


@before_model
async def refresh_slack_assistant_status_before_model(
    state: AgentState,
    runtime: Runtime,  # noqa: ARG001
) -> dict[str, Any] | None:
    try:
        config = get_config()
        configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
        slack_thread = configurable.get("slack_thread") if isinstance(configurable, dict) else None
        if not isinstance(slack_thread, dict):
            return None

        channel_id = slack_thread.get("channel_id")
        thread_ts = slack_thread.get("thread_ts")
        if not isinstance(channel_id, str) or not isinstance(thread_ts, str):
            return None
        if not channel_id or not thread_ts:
            return None

        messages = state.get("messages", []) if isinstance(state, dict) else []
        status = _status_from_recent_tool_calls(messages)

        await set_slack_assistant_status(
            channel_id,
            thread_ts,
            status=status,
            loading_messages=list(DEFAULT_LOADING_MESSAGES),
        )
    except Exception:
        logger.exception("Failed to refresh Slack assistant status")
    return None
