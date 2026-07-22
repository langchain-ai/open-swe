"""Post a closeout message back to a run's originating source channel.

Resolves the source channel from the run config (Slack thread, then Linear
issue, then GitHub PR/issue) and posts a single message so a failed run never
leaves an empty, abandoned thread.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from .github_app import get_github_app_installation_token
from .github_comments import post_github_comment
from .github_token import get_github_token
from .linear import comment_on_linear_issue
from .slack import post_slack_thread_reply

logger = logging.getLogger(__name__)


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


async def notify_source_channel(config: Mapping[str, Any], message: str) -> bool:
    """Post a message to the run's source channel (Slack, then Linear, then GitHub)."""
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        logger.info("No runtime configurable found for source-channel notification")
        return False

    slack_target = _get_slack_target(configurable)
    if slack_target is not None:
        channel_id, thread_ts = slack_target
        await post_slack_thread_reply(channel_id, thread_ts, message)
        logger.info("Sent source-channel notification to Slack thread %s", thread_ts)
        return True

    linear_issue_id = _get_linear_issue_id(configurable)
    if linear_issue_id is not None:
        await comment_on_linear_issue(linear_issue_id, message)
        logger.info("Sent source-channel notification to Linear issue %s", linear_issue_id)
        return True

    github_target = _get_github_target(configurable)
    if github_target is not None:
        token = get_github_token(config) or await get_github_app_installation_token()
        if not token:
            logger.info("No GitHub token available for source-channel notification")
            return False
        repo, issue_number = github_target
        await post_github_comment(repo, issue_number, message, token=token)
        logger.info("Sent source-channel notification to GitHub item #%s", issue_number)
        return True

    logger.info("No user-facing target found for source-channel notification")
    return False
