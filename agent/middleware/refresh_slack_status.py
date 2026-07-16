"""Middleware that keeps Slack's assistant status current during agent work.

Slack's ``assistant.threads.setStatus`` indicator expires after two minutes
if no message is sent. This middleware refreshes the indicator while model and
tool calls are actively running, then clears it when the run exits without
relying on the model to post a final Slack reply.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from ..utils.slack import (
    DEFAULT_ASSISTANT_STATUS,
    DEFAULT_LOADING_MESSAGES,
    set_slack_assistant_status,
)

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL_SECONDS = 60.0
_MAX_HEARTBEAT_SECONDS = 60 * 60

_T = TypeVar("_T")


# Tool-name -> human-readable status. Keep in sync with the tool list in
# agent/server.py, agent/reviewer.py, and the deepagents built-ins (read_file,
# write_file, edit_file, execute, glob, grep, task).
_TOOL_STATUS: dict[str, str] = {
    "read_file": "reading files...",
    "write_file": "editing files...",
    "edit_file": "editing files...",
    "execute": "running commands...",
    "glob": "scanning the repo...",
    "grep": "searching the codebase...",
    "task": "delegating to a subagent...",
    "web_search": "searching the web...",
    "fetch_url": "fetching a URL...",
    "http_request": "making an HTTP request...",
    "request_pr_review": "requesting a PR review...",
    "slack_add_reaction": "reacting in Slack...",
    "slack_read_thread_messages": "reading Slack history...",
    "slack_thread_reply": "drafting a Slack reply...",
    "linear_comment": "commenting on Linear...",
    "linear_create_issue": "creating a Linear issue...",
    "linear_get_issue": "checking Linear...",
    "linear_get_issue_comments": "checking Linear...",
    "linear_list_teams": "checking Linear...",
    "linear_search_issues": "searching Linear...",
    "linear_update_issue": "updating Linear...",
    "linear_delete_issue": "updating Linear...",
    "add_finding": "recording review findings...",
    "update_finding": "updating review findings...",
    "list_findings": "checking review findings...",
    "publish_review": "publishing the review...",
    "set_stackability_review": "recording the stackability review...",
    "publish_stackability_review": "publishing the stackability review...",
}


def _slack_thread_from_config() -> tuple[str, str] | None:
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
    return channel_id, thread_ts


def _tool_call_name(tool_call: object) -> str | None:
    if isinstance(tool_call, dict):
        name = tool_call.get("name")
    else:
        name = getattr(tool_call, "name", None)
    return name if isinstance(name, str) and name else None


def _status_from_recent_tool_calls(messages: list[Any]) -> str:
    """Pick a status string based on the last assistant message's tool calls."""
    for msg in reversed(messages):
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            continue
        # Use the first tool call's name; if the agent fans out, this is fine
        # as a single-line indicator.
        name = _tool_call_name(tool_calls[0])
        if isinstance(name, str) and name in _TOOL_STATUS:
            return _TOOL_STATUS[name]
        return DEFAULT_ASSISTANT_STATUS
    return DEFAULT_ASSISTANT_STATUS


async def _set_status(channel_id: str, thread_ts: str, status: str) -> None:
    try:
        await set_slack_assistant_status(
            channel_id,
            thread_ts,
            status=status,
            loading_messages=list(DEFAULT_LOADING_MESSAGES) if status else None,
        )
    except Exception:
        logger.exception("Failed to update Slack assistant status")


class SlackAssistantStatusMiddleware(AgentMiddleware):
    """Maintain Slack's assistant status for Slack-triggered agent runs."""

    state_schema = AgentState

    def __init__(
        self,
        *,
        heartbeat_interval_seconds: float = _HEARTBEAT_INTERVAL_SECONDS,
        max_heartbeat_seconds: float = _MAX_HEARTBEAT_SECONDS,
    ) -> None:
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._max_heartbeat_seconds = max_heartbeat_seconds

    async def abefore_agent(
        self,
        state: AgentState,  # noqa: ARG002
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        await self._try_set(DEFAULT_ASSISTANT_STATUS)
        return None

    async def aafter_agent(
        self,
        state: AgentState,  # noqa: ARG002
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        # Slack auto-clears on bot replies. This explicit clear covers soft
        # exits where the agent stops without posting a message.
        await self._try_set("")
        return None

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        messages = request.state.get("messages", []) if isinstance(request.state, dict) else []
        status = _status_from_recent_tool_calls(messages)
        return await self._run_with_heartbeat(status, handler(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        name = _tool_call_name(request.tool_call)
        status = _TOOL_STATUS.get(name or "", DEFAULT_ASSISTANT_STATUS)
        return await self._run_with_heartbeat(status, handler(request))

    async def _try_set(self, status: str) -> None:
        try:
            slack_thread = _slack_thread_from_config()
            if slack_thread is None:
                return
            channel_id, thread_ts = slack_thread
            await _set_status(channel_id, thread_ts, status)
        except Exception:
            logger.exception("Failed to read Slack thread config")

    async def _run_with_heartbeat(self, status: str, awaitable: Awaitable[_T]) -> _T:
        try:
            slack_thread = _slack_thread_from_config()
        except Exception:
            logger.exception("Failed to read Slack thread config")
            return await awaitable
        if slack_thread is None:
            return await awaitable

        channel_id, thread_ts = slack_thread
        await _set_status(channel_id, thread_ts, status)
        heartbeat = asyncio.create_task(self._heartbeat(channel_id, thread_ts, status))
        try:
            return await awaitable
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat(self, channel_id: str, thread_ts: str, status: str) -> None:
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            if loop.time() - started_at >= self._max_heartbeat_seconds:
                logger.info(
                    "Stopping Slack assistant status heartbeat after %.0f seconds",
                    self._max_heartbeat_seconds,
                )
                return
            await _set_status(channel_id, thread_ts, status)
