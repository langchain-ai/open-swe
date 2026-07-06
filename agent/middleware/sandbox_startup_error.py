"""Catch startup-time sandbox errors and post a closeout to the source thread."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langgraph.config import get_config

from ..utils.github_app import get_github_app_installation_token
from ..utils.github_comments import post_github_comment
from ..utils.github_token import get_github_token
from ..utils.linear import comment_on_linear_issue
from ..utils.sandbox_state import SandboxBackendNotReady
from ..utils.slack import post_slack_thread_reply

logger = logging.getLogger(__name__)

STARTUP_ERROR_MESSAGE = (
    "Your session couldn't start — the sandbox backend was not ready. Please retry the mention."
)
_STARTUP_ERROR_MARKER = "Sandbox startup error"


def _is_sandbox_backend_error(exc: BaseException) -> bool:
    if isinstance(exc, SandboxBackendNotReady):
        return True
    if isinstance(exc, RuntimeError):
        return "sandbox backend" in str(exc).lower()
    return False


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


async def _post_startup_error_notification(config: Mapping[str, Any]) -> None:
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        logger.info("No runtime configurable found for sandbox startup error notification")
        return

    slack_target = _get_slack_target(configurable)
    if slack_target is not None:
        channel_id, thread_ts = slack_target
        await post_slack_thread_reply(channel_id, thread_ts, STARTUP_ERROR_MESSAGE)
        logger.info("Sent sandbox startup error notification to Slack thread %s", thread_ts)
        return

    linear_issue_id = _get_linear_issue_id(configurable)
    if linear_issue_id is not None:
        await comment_on_linear_issue(linear_issue_id, STARTUP_ERROR_MESSAGE)
        logger.info("Sent sandbox startup error notification to Linear issue %s", linear_issue_id)
        return

    github_target = _get_github_target(configurable)
    if github_target is not None:
        token = get_github_token(config) or await get_github_app_installation_token()
        if not token:
            logger.info("No GitHub token available for sandbox startup error notification")
            return
        repo, issue_number = github_target
        await post_github_comment(
            repo,
            issue_number,
            STARTUP_ERROR_MESSAGE,
            token=token,
        )
        logger.info("Sent sandbox startup error notification to GitHub item #%s", issue_number)
        return

    logger.info("No user-facing target found for sandbox startup error notification")


async def _handle_startup_error(exc: BaseException) -> dict[str, Any]:
    logger.exception("Sandbox startup error before first LLM turn: %s", exc)
    try:
        config = get_config()
        await _post_startup_error_notification(config)
    except Exception:
        logger.exception("Failed to send sandbox startup error notification")
    content = f"{_STARTUP_ERROR_MARKER}: {exc}. {STARTUP_ERROR_MESSAGE}"
    return {"jump_to": "end", "messages": [AIMessage(content=content)]}


class SandboxStartupErrorMiddleware(AgentMiddleware[AgentState, Any]):
    """Notify the user when a sandbox error kills the run before the first LLM turn."""

    state_schema = AgentState

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | Any:
        try:
            return await handler(request)
        except Exception as exc:
            if not _is_sandbox_backend_error(exc):
                raise
            return await _handle_startup_error(exc)
