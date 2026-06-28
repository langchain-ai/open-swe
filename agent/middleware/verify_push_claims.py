"""Block slack_thread_reply / linear_comment / gh-comment tool calls that
claim a push happened when no `git push` (or `open_pull_request`) call
appears earlier in the trajectory.

The system prompt already requires a push after every commit, but compliance
slips: the agent commits locally, then posts "Pushed …" to Slack while the
remote SHA never changes. This middleware reads the recent tool-call history
on `request.state` and, when the outgoing message asserts a push, rejects
the reply with an actionable error so the model retries after actually
pushing.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

logger = logging.getLogger(__name__)

_PUSH_CLAIM = re.compile(r"\b(pushed|push(ing)? to|force[- ]push(ed)?)\b", re.IGNORECASE)
_PUSH_EVIDENCE = re.compile(r"git\s+(-C\s+\S+\s+)?push\b|gh\s+pr\s+push\b", re.IGNORECASE)
_GH_COMMENT = re.compile(r"\bgh\s+(pr|issue)\s+comment\b", re.IGNORECASE)
_TRAJECTORY_LOOKBACK = 50

_GUARDED_MESSAGE_ARGS = {
    "slack_thread_reply": ("message",),
    "linear_comment": ("comment_body",),
}


def _tool_call(request: ToolCallRequest) -> Mapping[str, Any] | None:
    tool_call = getattr(request, "tool_call", None)
    return tool_call if isinstance(tool_call, Mapping) else None


def _tool_args(request: ToolCallRequest) -> Mapping[str, Any]:
    call = _tool_call(request)
    args = call.get("args") if call else None
    return args if isinstance(args, Mapping) else {}


def _tool_call_id(request: ToolCallRequest) -> str:
    call = _tool_call(request)
    value = call.get("id") if call else None
    return value if isinstance(value, str) else ""


def _state_messages(request: ToolCallRequest) -> list[Any]:
    state = getattr(request, "state", None)
    if isinstance(state, Mapping):
        messages = state.get("messages")
        if isinstance(messages, list):
            return messages
    return []


def _claim_text(request: ToolCallRequest) -> str | None:
    call = _tool_call(request)
    if call is None:
        return None
    name = call.get("name")
    if not isinstance(name, str):
        return None
    args = _tool_args(request)
    if name in _GUARDED_MESSAGE_ARGS:
        for arg_name in _GUARDED_MESSAGE_ARGS[name]:
            value = args.get(arg_name)
            if isinstance(value, str) and value.strip():
                return value
        return None
    if name == "execute":
        command = args.get("command")
        if isinstance(command, str) and _GH_COMMENT.search(command):
            return command
    return None


def _observed_push(messages: list[Any]) -> bool:
    for msg in reversed(messages[-_TRAJECTORY_LOOKBACK:]):
        tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in tool_calls:
            if not isinstance(tc, Mapping):
                continue
            name = tc.get("name")
            if name == "open_pull_request":
                return True
            if name in {"execute", "run_bash"}:
                args = tc.get("args")
                command = args.get("command") if isinstance(args, Mapping) else None
                if isinstance(command, str) and _PUSH_EVIDENCE.search(command):
                    return True
    return False


def _rejection(tool_call_id: str, tool_name: str) -> ToolMessage:
    content = (
        f"`{tool_name}` blocked: the message claims a push but no `git push` "
        "(or successful `open_pull_request`) call appears in the recent "
        "trajectory. Run `git push origin <branch>` (or call "
        "`open_pull_request`) first, confirm the new head SHA on the remote, "
        "then retry this reply."
    )
    return ToolMessage(content=content, tool_call_id=tool_call_id, status="error")


class VerifyPushClaimsMiddleware(AgentMiddleware):
    """Reject closeout replies that assert a push without trajectory evidence."""

    state_schema = AgentState

    def _maybe_reject(self, request: ToolCallRequest) -> ToolMessage | None:
        text = _claim_text(request)
        if not text or not _PUSH_CLAIM.search(text):
            return None
        if _observed_push(_state_messages(request)):
            return None
        call = _tool_call(request)
        name = call.get("name") if call else ""
        tool_name = name if isinstance(name, str) else ""
        logger.warning(
            "push_claim_blocked=true tool=%s tool_call_id=%s",
            tool_name,
            _tool_call_id(request),
        )
        return _rejection(_tool_call_id(request), tool_name)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        rejection = self._maybe_reject(request)
        if rejection is not None:
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        rejection = self._maybe_reject(request)
        if rejection is not None:
            return rejection
        return await handler(request)
