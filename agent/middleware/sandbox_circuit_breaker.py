"""Circuit breaker for repeated unrecoverable sandbox failures."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..utils.github_app import get_github_app_installation_token
from ..utils.github_comments import post_github_comment
from ..utils.github_token import get_github_token
from ..utils.linear import comment_on_linear_issue
from ..utils.slack import post_slack_thread_reply

logger = logging.getLogger(__name__)

SANDBOX_CIRCUIT_BREAKER_THRESHOLD = 2
SANDBOX_UNRECOVERABLE_MESSAGE = "Sandbox became unrecoverable mid-task. Please retrigger."

_CIRCUIT_BREAKER_MARKER = "Sandbox circuit breaker triggered"
_SANDBOX_RECREATED_AFTER_CLIENT_ERROR = "sandbox_recreated_after_client_error"
_SANDBOX_ID_RE = re.compile(r"\bsb-[A-Za-z0-9-]+\b")


@dataclass(frozen=True)
class SandboxErrorStreak:
    reason: Literal["client_error", "recreated"]
    sandbox_id: str | None
    count: int


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


def _last_message_has_circuit_breaker_marker(messages: Sequence[BaseMessage]) -> bool:
    if not messages:
        return False
    content = _content_to_text(getattr(messages[-1], "content", "") or "")
    return _CIRCUIT_BREAKER_MARKER in content


def _sandbox_error_streak(messages: Sequence[BaseMessage]) -> SandboxErrorStreak | None:
    sandbox_id: str | None = None
    reason: Literal["client_error", "recreated"] | None = None
    count = 0

    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            text = _content_to_text(message.content)
            if _SANDBOX_RECREATED_AFTER_CLIENT_ERROR in text:
                if reason is None:
                    reason = "recreated"
                elif reason != "recreated":
                    break
                count += 1
                continue

            message_sandbox_id = _extract_sandbox_id(text)
            if "SandboxClientError" not in text or message_sandbox_id is None:
                break
            if reason is None:
                reason = "client_error"
                sandbox_id = message_sandbox_id
            elif reason != "client_error" or message_sandbox_id != sandbox_id:
                break
            count += 1
            continue

        text = _content_to_text(getattr(message, "content", "") or "")
        if _CIRCUIT_BREAKER_MARKER in text:
            return None
        if getattr(message, "type", "") in {"human", "system"}:
            break

    if reason is None:
        return None
    return SandboxErrorStreak(reason=reason, sandbox_id=sandbox_id, count=count)


def _get_slack_target(configurable: Mapping[str, Any]) -> tuple[str, str] | None:
    slack_thread = configurable.get("slack_thread")
    if not isinstance(slack_thread, Mapping):
        return None
    channel_id = slack_thread.get("channel_id")
    thread_ts = slack_thread.get("thread_ts")
    if not isinstance(channel_id, str) or not isinstance(thread_ts, str):
        return None
    if not channel_id or not thread_ts:
        return None
    return channel_id, thread_ts


def _get_linear_issue_id(configurable: Mapping[str, Any]) -> str | None:
    linear_issue = configurable.get("linear_issue")
    if not isinstance(linear_issue, Mapping):
        return None
    issue_id = linear_issue.get("id")
    return issue_id if isinstance(issue_id, str) and issue_id else None


def _coerce_issue_number(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _get_github_target(configurable: Mapping[str, Any]) -> tuple[dict[str, str], int] | None:
    repo_config = configurable.get("repo")
    if not isinstance(repo_config, Mapping):
        return None
    owner = repo_config.get("owner")
    name = repo_config.get("name")
    if not isinstance(owner, str) or not isinstance(name, str) or not owner or not name:
        return None
    repo = {"owner": owner, "name": name}

    github_pr_or_issue = configurable.get("github_pr_or_issue")
    if isinstance(github_pr_or_issue, Mapping):
        number = _coerce_issue_number(github_pr_or_issue.get("number"))
        target_repo = github_pr_or_issue.get("repo")
        if isinstance(target_repo, Mapping):
            target_owner = target_repo.get("owner")
            target_name = target_repo.get("name")
            if isinstance(target_owner, str) and isinstance(target_name, str):
                repo = {"owner": target_owner, "name": target_name}
        if number is not None:
            return repo, number

    github_issue = configurable.get("github_issue")
    if isinstance(github_issue, Mapping):
        number = _coerce_issue_number(github_issue.get("number"))
        if number is not None:
            return repo, number

    pr_number = _coerce_issue_number(configurable.get("pr_number"))
    if pr_number is not None:
        return repo, pr_number
    return None


async def _post_unrecoverable_notification(config: Mapping[str, Any]) -> None:
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        logger.info("No runtime configurable found for sandbox circuit breaker notification")
        return

    slack_target = _get_slack_target(configurable)
    if slack_target is not None:
        channel_id, thread_ts = slack_target
        await post_slack_thread_reply(channel_id, thread_ts, SANDBOX_UNRECOVERABLE_MESSAGE)
        logger.info("Sent sandbox circuit breaker notification to Slack thread %s", thread_ts)
        return

    linear_issue_id = _get_linear_issue_id(configurable)
    if linear_issue_id is not None:
        await comment_on_linear_issue(linear_issue_id, SANDBOX_UNRECOVERABLE_MESSAGE)
        logger.info("Sent sandbox circuit breaker notification to Linear issue %s", linear_issue_id)
        return

    github_target = _get_github_target(configurable)
    if github_target is not None:
        token = get_github_token(config) or await get_github_app_installation_token()
        if not token:
            logger.info("No GitHub token available for sandbox circuit breaker notification")
            return
        repo, issue_number = github_target
        await post_github_comment(
            repo,
            issue_number,
            SANDBOX_UNRECOVERABLE_MESSAGE,
            token=token,
        )
        logger.info("Sent sandbox circuit breaker notification to GitHub item #%s", issue_number)
        return

    logger.info("No user-facing target found for sandbox circuit breaker notification")


class SandboxCircuitBreakerMiddleware(AgentMiddleware[AgentState, Any]):
    """Stop runs that repeatedly hit the same dead sandbox."""

    state_schema = AgentState

    def __init__(self, *, threshold: int = SANDBOX_CIRCUIT_BREAKER_THRESHOLD) -> None:
        self.threshold = threshold

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:  # noqa: ARG002
        messages = state.get("messages", [])
        if _last_message_has_circuit_breaker_marker(messages):
            return None

        streak = _sandbox_error_streak(messages)
        if streak is None or streak.count <= self.threshold:
            return None

        if streak.reason == "recreated":
            detail = (
                f"{streak.count} consecutive sandbox recreations did not recover tool execution"
            )
        else:
            detail = f"{streak.count} consecutive sandbox tool failures against {streak.sandbox_id}"
        content = f"{_CIRCUIT_BREAKER_MARKER}: {detail}. {SANDBOX_UNRECOVERABLE_MESSAGE}"
        return {"jump_to": "end", "messages": [AIMessage(content=content)]}

    @hook_config(can_jump_to=["end"])
    async def abefore_model(
        self,
        state: AgentState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    async def aafter_agent(
        self,
        state: AgentState,
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages:
            return None

        last_msg = messages[-1]
        content = _content_to_text(getattr(last_msg, "content", "") or "")
        if _CIRCUIT_BREAKER_MARKER not in content:
            return None

        try:
            config = get_config()
            await _post_unrecoverable_notification(config)
        except Exception:
            logger.exception("Failed to send sandbox circuit breaker notification")

        return None
