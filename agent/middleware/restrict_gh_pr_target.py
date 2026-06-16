"""Reject `execute` calls that target a PR other than the configured one.

The reviewer is configured with a single ``pr_number`` from
``config["configurable"]``. The ``execute`` tool, however, accepts arbitrary
shell commands — including ``gh pr diff <N>`` for any N. In production we saw
the model fetch a different PR's diff after spotting a reference like
"follow-up to #X" in the diff body and publish "no inline findings" against
the wrong target. This middleware enforces the invariant at the tool layer:
any ``gh pr <verb> <number>`` whose number differs from the configured
``pr_number`` is rejected with a ``ToolMessage`` so the agent can self-correct.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

# `gh pr <verb> <number>` shapes that target a specific PR. `create` takes no
# PR number (it opens a new one) and is intentionally excluded.
_PR_TARGETED_VERBS = frozenset(
    {
        "diff",
        "view",
        "checkout",
        "review",
        "merge",
        "close",
        "comment",
        "edit",
        "ready",
        "reopen",
        "lock",
        "unlock",
    }
)


def _configured_pr_number(request: ToolCallRequest) -> int | None:
    runtime_config = getattr(getattr(request, "runtime", None), "config", None)
    config: Mapping[str, Any] | None = (
        runtime_config if isinstance(runtime_config, Mapping) else None
    )
    if config is None:
        try:
            maybe_config = get_config()
        except Exception:  # noqa: BLE001
            return None
        config = maybe_config if isinstance(maybe_config, Mapping) else None
    if config is None:
        return None
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        return None
    pr_number = configurable.get("pr_number")
    return pr_number if isinstance(pr_number, int) else None


def _tool_call_id(request: ToolCallRequest) -> str | None:
    if isinstance(request.tool_call, dict):
        return request.tool_call.get("id")
    return None


def _command(request: ToolCallRequest) -> str | None:
    tool_call = getattr(request, "tool_call", None)
    if not isinstance(tool_call, Mapping):
        return None
    if tool_call.get("name") != "execute":
        return None
    args = tool_call.get("args")
    if not isinstance(args, Mapping):
        return None
    command = args.get("command")
    return command if isinstance(command, str) and command else None


def _foreign_pr_target(command: str, configured_pr: int) -> tuple[str, int] | None:
    """Return ``(verb, foreign_pr_number)`` when the command targets a different PR.

    Tokenises with ``shlex`` so env-var prefixes (``GH_TOKEN=dummy gh ...``),
    flags (``--repo owner/name``), and stray quoting don't fool the match.
    A PR number is recognized as the first bare integer token (optionally with
    a leading ``#``) appearing after ``gh pr <verb>``.
    """
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    i = 0
    while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("-"):
        i += 1
    if i >= len(tokens) or tokens[i] != "gh":
        return None
    i += 1
    if i >= len(tokens) or tokens[i] != "pr":
        return None
    i += 1
    if i >= len(tokens):
        return None
    verb = tokens[i]
    if verb not in _PR_TARGETED_VERBS:
        return None
    i += 1
    for tok in tokens[i:]:
        if tok.startswith("-"):
            continue
        candidate = tok[1:] if tok.startswith("#") else tok
        if re.fullmatch(r"\d+", candidate):
            number = int(candidate)
            if number != configured_pr:
                return verb, number
            return None
    return None


def _rejection_message(
    request: ToolCallRequest,
    *,
    verb: str,
    foreign_pr: int,
    configured_pr: int,
) -> ToolMessage:
    payload = {
        "status": "error",
        "error_type": "ForeignPRTargetRejected",
        "name": "execute",
        "error": (
            f"Refused to run `gh pr {verb} {foreign_pr}`: this reviewer is "
            f"scoped to PR #{configured_pr}. The diff or commit messages may "
            f"reference other PRs, but only PR #{configured_pr} may be "
            f"targeted by `gh pr` commands. To read context from PR "
            f"#{foreign_pr}, open the relevant files in the checked-out repo "
            f"instead."
        ),
    }
    return ToolMessage(
        content=json.dumps(payload),
        tool_call_id=_tool_call_id(request),
        name="execute",
        status="error",
    )


class RestrictGhPrTargetMiddleware(AgentMiddleware):
    """Reject `execute` calls that run `gh pr <verb>` against a foreign PR."""

    state_schema = AgentState

    def _maybe_reject(self, request: ToolCallRequest) -> ToolMessage | None:
        command = _command(request)
        if command is None:
            return None
        configured_pr = _configured_pr_number(request)
        if configured_pr is None:
            return None
        match = _foreign_pr_target(command, configured_pr)
        if match is None:
            return None
        verb, foreign_pr = match
        logger.warning(
            "Rejecting `gh pr %s %s` from execute tool; reviewer is scoped to PR #%s",
            verb,
            foreign_pr,
            configured_pr,
        )
        return _rejection_message(
            request,
            verb=verb,
            foreign_pr=foreign_pr,
            configured_pr=configured_pr,
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        rejection = self._maybe_reject(request)
        if rejection is not None:
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        rejection = self._maybe_reject(request)
        if rejection is not None:
            return rejection
        return await handler(request)
