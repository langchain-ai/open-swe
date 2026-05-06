"""After-agent middleware that notifies users when the agent terminates with
an unhandled provider/transport error.

This is a fail-safe that complements the client-level retry policy configured
in :mod:`agent.utils.model`. Even with retries, exhaustion or other transient
failures should not equal silence — the user should always learn that their
request was received and why it could not be completed.

The middleware mirrors the structure of :mod:`agent.middleware.notify_step_limit`:
it runs `@after_agent`, inspects the final state for an error marker, and posts
a one-line user-facing reply to whichever surface triggered the run (Slack,
Linear, or GitHub).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import AgentState, after_agent
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..utils.github_comments import post_github_comment
from ..utils.linear import comment_on_linear_issue
from ..utils.slack import post_slack_thread_reply

logger = logging.getLogger(__name__)


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


def _extract_error(state: AgentState) -> tuple[str, str] | None:
    """Return ``(error_class, error_message)`` if state carries an unhandled error.

    Two shapes are recognized:

    1. A graph-level ``error`` key set by an outer try/except wrapper
       (``state["error"]`` may be an Exception or a ``{"type": str, "message": str}`` dict).
    2. A trailing AI message whose content begins with ``"Provider error:"`` —
       used by the model executor when it cannot produce a normal response.

    Returns ``None`` when no error is detected.
    """
    err = state.get("error") if isinstance(state, Mapping) else None
    if err is not None:
        if isinstance(err, BaseException):
            return type(err).__name__, str(err) or type(err).__name__
        if isinstance(err, Mapping):
            err_type = err.get("type") or err.get("class") or "ProviderError"
            err_msg = err.get("message") or str(err)
            return str(err_type), str(err_msg)
        return "Error", str(err)

    messages = state.get("messages", []) if isinstance(state, Mapping) else []
    if not messages:
        return None
    last = messages[-1]
    text = _content_to_text(getattr(last, "content", "") or "")
    marker = "Provider error:"
    if marker in text:
        # Best-effort parse: "Provider error: <ClassName>: <message>"
        rest = text.split(marker, 1)[1].strip()
        if ":" in rest:
            cls, msg = rest.split(":", 1)
            return cls.strip() or "ProviderError", msg.strip() or rest
        return "ProviderError", rest or text
    return None


def _format_message(error_class: str) -> str:
    return (
        f"I hit a provider error and couldn't complete your task: {error_class}. "
        "Please retry."
    )


async def _notify_slack(configurable: Mapping[str, Any], message: str) -> bool:
    slack_thread = configurable.get("slack_thread")
    if not isinstance(slack_thread, Mapping):
        return False
    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if not (
        isinstance(channel_id, str)
        and isinstance(thread_ts, str)
        and channel_id
        and thread_ts
    ):
        return False
    try:
        await post_slack_thread_reply(channel_id, thread_ts, message)
        logger.info("Sent unhandled-error notification to Slack thread %s", thread_ts)
    except Exception:
        logger.exception("Failed to send unhandled-error notification to Slack")
    return True


async def _notify_linear(configurable: Mapping[str, Any], message: str) -> bool:
    linear_issue = configurable.get("linear_issue")
    if not isinstance(linear_issue, Mapping):
        return False
    issue_id = linear_issue.get("id")
    if not (isinstance(issue_id, str) and issue_id):
        return False
    try:
        await comment_on_linear_issue(issue_id, message)
        logger.info("Sent unhandled-error notification to Linear issue %s", issue_id)
    except Exception:
        logger.exception("Failed to send unhandled-error notification to Linear")
    return True


async def _notify_github(configurable: Mapping[str, Any], message: str) -> bool:
    target = configurable.get("github_pr_or_issue")
    if not isinstance(target, Mapping):
        return False
    repo_config = target.get("repo")
    issue_number = target.get("number")
    token = target.get("token")
    if not (
        isinstance(repo_config, Mapping)
        and isinstance(issue_number, int)
        and isinstance(token, str)
        and token
    ):
        return False
    try:
        await post_github_comment(dict(repo_config), issue_number, message, token=token)
        logger.info(
            "Sent unhandled-error notification to GitHub %s/%s#%s",
            repo_config.get("owner"),
            repo_config.get("name"),
            issue_number,
        )
    except Exception:
        logger.exception("Failed to send unhandled-error notification to GitHub")
    return True


@after_agent
async def notify_unhandled_error(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """Notify the user when the agent terminates with an unhandled error.

    Runs after the agent exits. If the final state carries an error marker
    (either ``state["error"]`` or a trailing ``"Provider error: ..."`` AI
    message), posts a single one-line reply to whichever surface triggered
    the run so the user is never left in silence — even when the
    client-level retry policy in :func:`agent.utils.model.make_model` is
    exhausted.
    """
    error = _extract_error(state)
    if error is None:
        return None

    error_class, _error_message = error
    message = _format_message(error_class)

    config = get_config()
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        logger.info(
            "No configurable on runtime — cannot send unhandled-error notification"
        )
        return None

    # Only post once: the first matching surface wins. Run order mirrors how
    # the agent identifies its triggering surface (Slack -> Linear -> GitHub).
    for notify in (_notify_slack, _notify_linear, _notify_github):
        if await notify(configurable, message):
            return None

    logger.info(
        "No Slack/Linear/GitHub surface configured — cannot send unhandled-error notification"
    )
    return None
