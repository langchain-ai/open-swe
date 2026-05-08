"""Circuit breaker for repeated mid-run sandbox failures.

If the agent keeps hitting ``SandboxClientError`` against the same
``sb-<id>`` even after the tool-error middleware has tried to recreate
the sandbox, that's a signal the recreation path is itself failing. We
short-circuit the run after a small number of consecutive same-sandbox
failures and post a user-facing notification so the user is not left
waiting on a dead run that only an outer ``CancelledError`` can clean
up.

Mirrors the dispatch shape of ``notify_step_limit_reached``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import AgentState, after_agent
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..utils.slack import post_slack_thread_reply

logger = logging.getLogger(__name__)

# Number of consecutive SandboxClientError ToolMessages against the same
# sb-<id> that must be observed before we trip the breaker. Recreate-and-
# retry already happens once per failure inside ToolErrorMiddleware, so
# tripping at >2 means we've seen "death, recreate, death, recreate, death"
# — recovery is clearly not working.
SANDBOX_CIRCUIT_BREAKER_THRESHOLD = 2

# Marker text written into the AIMessage when the breaker trips. Used by
# downstream consumers (and tests) to detect the trip.
SANDBOX_CIRCUIT_BREAKER_MARKER = "Sandbox became unrecoverable mid-task"

_USER_FACING_MESSAGE = (
    "Sandbox became unrecoverable mid-task. Please retrigger."
)

_SANDBOX_ID_RE = re.compile(r"sb-[0-9a-fA-F]+")
_SANDBOX_ERROR_MARKER = "SandboxClientError"


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


def _extract_sandbox_id(text: str) -> str | None:
    match = _SANDBOX_ID_RE.search(text)
    return match.group(0) if match else None


def _consecutive_same_sandbox_failures(messages: list[Any]) -> tuple[int, str | None]:
    """Walk messages from the tail counting consecutive sandbox failures.

    A "sandbox failure" is a ``ToolMessage`` whose stringified content
    contains ``SandboxClientError``. Failures must reference the same
    ``sb-<id>`` to count toward the streak; the first non-matching
    ToolMessage breaks the streak. Non-ToolMessage entries are skipped.
    """
    streak = 0
    sandbox_id: str | None = None
    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            # Allow AI/Human messages to interleave without breaking the streak;
            # only a *successful* ToolMessage (one that doesn't match our marker)
            # should break it.
            continue
        text = _content_to_text(getattr(msg, "content", "") or "")
        if _SANDBOX_ERROR_MARKER not in text:
            break
        this_id = _extract_sandbox_id(text)
        if sandbox_id is None:
            sandbox_id = this_id
        elif this_id is not None and this_id != sandbox_id:
            break
        streak += 1
    return streak, sandbox_id


@after_agent
async def sandbox_circuit_breaker(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """Notify the user when consecutive sandbox failures exceed the threshold.

    Runs after the agent exits. If the tail of the message history shows
    more than ``SANDBOX_CIRCUIT_BREAKER_THRESHOLD`` consecutive
    ``SandboxClientError`` ToolMessages against the same ``sb-<id>``, we
    consider the sandbox unrecoverable and post a user-facing reply via
    whichever notification channel is configured for this run. The hook
    returns ``None`` (matching ``notify_step_limit_reached``); the agent
    has already exited, so there is no graph mutation to perform here.
    """
    messages = state.get("messages", [])
    if not messages:
        return None

    streak, sandbox_id = _consecutive_same_sandbox_failures(messages)
    if streak <= SANDBOX_CIRCUIT_BREAKER_THRESHOLD:
        return None

    logger.warning(
        "Sandbox circuit breaker tripped: %d consecutive failures against %s",
        streak,
        sandbox_id or "<unknown sb-id>",
    )

    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    if not isinstance(configurable, dict):
        configurable = {}

    slack_thread = configurable.get("slack_thread")
    if isinstance(slack_thread, dict):
        channel_id = slack_thread.get("channel_id")
        thread_ts = slack_thread.get("thread_ts")
        if (
            isinstance(channel_id, str)
            and isinstance(thread_ts, str)
            and channel_id
            and thread_ts
        ):
            try:
                await post_slack_thread_reply(channel_id, thread_ts, _USER_FACING_MESSAGE)
                logger.info(
                    "Sent sandbox-circuit-breaker notification to Slack thread %s",
                    thread_ts,
                )
                return None
            except Exception:
                logger.exception(
                    "Failed to send sandbox-circuit-breaker notification to Slack"
                )

    linear_issue = configurable.get("linear_issue")
    if isinstance(linear_issue, dict):
        issue_id = linear_issue.get("id")
        if isinstance(issue_id, str) and issue_id:
            try:
                from ..utils.linear import comment_on_linear_issue  # noqa: PLC0415

                await comment_on_linear_issue(issue_id, _USER_FACING_MESSAGE)
                logger.info(
                    "Sent sandbox-circuit-breaker notification to Linear issue %s",
                    issue_id,
                )
                return None
            except Exception:
                logger.exception(
                    "Failed to send sandbox-circuit-breaker notification to Linear"
                )

    github_pr_or_issue = configurable.get("github_pr_or_issue")
    if isinstance(github_pr_or_issue, dict):
        # The repo doesn't currently expose a generic GitHub-comment helper at
        # the middleware layer, so we log the channel for now and leave the
        # actual delivery to whatever consumer wires this up.
        logger.info(
            "GitHub channel configured for sandbox-circuit-breaker but no helper "
            "is wired up; issue payload=%r",
            github_pr_or_issue,
        )

    logger.info(
        "No notification channel could deliver sandbox-circuit-breaker message "
        "(streak=%d, sandbox=%s)",
        streak,
        sandbox_id or "<unknown sb-id>",
    )
    return None
