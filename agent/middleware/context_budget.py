"""Hard input-token budget guard that runs before each model call."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..utils.github_app import get_github_app_installation_token
from ..utils.github_comments import post_github_comment
from ..utils.github_token import get_github_token
from ..utils.linear import comment_on_linear_issue
from ..utils.slack import post_slack_thread_reply

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_BUDGET_INPUT_TOKENS = 1_000_000
CONTEXT_BUDGET_MESSAGE = (
    "I stopped this session because its accumulated context grew past the configured "
    "token budget. The task may be incomplete — retry with a more focused request or "
    "ask me to continue from a checkpoint."
)

_BUDGET_MARKER = "Context budget exceeded"
# Conservative chars-per-token used when provider usage metadata isn't on the messages.
_CHARS_PER_TOKEN = 4


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


def _usage_input_tokens(message: BaseMessage) -> int | None:
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, Mapping):
        value = usage.get("input_tokens")
        if isinstance(value, int) and value >= 0:
            return value
    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(response_metadata, Mapping):
        token_usage = response_metadata.get("token_usage") or response_metadata.get("usage")
        if isinstance(token_usage, Mapping):
            for key in ("prompt_tokens", "input_tokens"):
                value = token_usage.get(key)
                if isinstance(value, int) and value >= 0:
                    return value
    return None


def _latest_reported_input_tokens(messages: Sequence[BaseMessage]) -> int | None:
    for message in reversed(messages):
        tokens = _usage_input_tokens(message)
        if tokens is not None:
            return tokens
    return None


def _estimate_input_tokens(messages: Sequence[BaseMessage]) -> int:
    total_chars = 0
    for message in messages:
        total_chars += len(_content_to_text(getattr(message, "content", "") or ""))
        tool_calls = getattr(message, "tool_calls", None)
        if isinstance(tool_calls, list):
            for call in tool_calls:
                if isinstance(call, Mapping):
                    args = call.get("args")
                    total_chars += len(str(args)) if args is not None else 0
                    name = call.get("name")
                    if isinstance(name, str):
                        total_chars += len(name)
    return total_chars // _CHARS_PER_TOKEN


def _last_message_has_budget_marker(messages: Sequence[BaseMessage]) -> bool:
    if not messages:
        return False
    content = _content_to_text(getattr(messages[-1], "content", "") or "")
    return _BUDGET_MARKER in content


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


async def _post_budget_notification(config: Mapping[str, Any]) -> None:
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        logger.info("No runtime configurable found for context budget notification")
        return

    slack_target = _get_slack_target(configurable)
    if slack_target is not None:
        channel_id, thread_ts = slack_target
        await post_slack_thread_reply(channel_id, thread_ts, CONTEXT_BUDGET_MESSAGE)
        logger.info("Sent context budget notification to Slack thread %s", thread_ts)
        return

    linear_issue_id = _get_linear_issue_id(configurable)
    if linear_issue_id is not None:
        await comment_on_linear_issue(linear_issue_id, CONTEXT_BUDGET_MESSAGE)
        logger.info("Sent context budget notification to Linear issue %s", linear_issue_id)
        return

    github_target = _get_github_target(configurable)
    if github_target is not None:
        token = get_github_token(config) or await get_github_app_installation_token()
        if not token:
            logger.info("No GitHub token available for context budget notification")
            return
        repo, issue_number = github_target
        await post_github_comment(
            repo,
            issue_number,
            CONTEXT_BUDGET_MESSAGE,
            token=token,
        )
        logger.info("Sent context budget notification to GitHub item #%s", issue_number)
        return

    logger.info("No user-facing target found for context budget notification")


class ContextBudgetMiddleware(AgentMiddleware[AgentState, Any]):
    """End the run before model calls if accumulated input tokens exceed the budget."""

    state_schema = AgentState

    def __init__(
        self,
        *,
        max_input_tokens: int = DEFAULT_CONTEXT_BUDGET_INPUT_TOKENS,
    ) -> None:
        self.max_input_tokens = max_input_tokens

    def _current_input_tokens(self, messages: Sequence[BaseMessage]) -> int:
        reported = _latest_reported_input_tokens(messages)
        if reported is not None:
            return reported
        return _estimate_input_tokens(messages)

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:  # noqa: ARG002
        messages = state.get("messages", [])
        if _last_message_has_budget_marker(messages):
            return None

        tokens = self._current_input_tokens(messages)
        if tokens < self.max_input_tokens:
            return None

        content = (
            f"{_BUDGET_MARKER}: ~{tokens} input tokens accumulated "
            f"(budget {self.max_input_tokens}). {CONTEXT_BUDGET_MESSAGE}"
        )
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
        if _BUDGET_MARKER not in content:
            return None

        try:
            config = get_config()
            await _post_budget_notification(config)
        except Exception:
            logger.exception("Failed to send context budget notification")

        return None
