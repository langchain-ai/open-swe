"""Telling the user, on the channel they triggered from, that their sandbox went quiet."""

import logging
import re
from collections.abc import Mapping
from typing import Any

from langgraph_sdk import get_client

from agent.auth.github_app import get_github_app_installation_token
from agent.auth.thread_token import get_github_token
from agent.platforms.github.comments import post_github_comment
from agent.platforms.linear.client import comment_on_linear_issue
from agent.platforms.slack.client import (
    LANGGRAPH_URL,
    get_active_slack_thread,
    post_slack_thread_reply,
)
from agent.utils.user_messages import warning

logger = logging.getLogger(__name__)


def sandbox_unreachable_message(
    *,
    sandbox_id: str | None = None,
    sandbox_name: str | None = None,
    replacement_attempted: bool = False,
) -> str:
    """User-facing text for a sandbox that stopped answering.

    Deliberately does not claim the sandbox is gone for good — all we observed is
    that it stopped responding, and it may come back. ``replacement_attempted``
    is for callers allowed to replace an unreachable sandbox (the read-only
    reviewer), where "Open SWE will not start a replacement" would be untrue.
    """
    identifiers = [
        part
        for part in (
            f"name {sandbox_name}" if sandbox_name else None,
            f"id {sandbox_id}" if sandbox_id else None,
        )
        if part
    ]
    which = f" ({', '.join(identifiers)})" if identifiers else ""
    if replacement_attempted:
        return warning(
            f"This thread's sandbox{which} stopped responding and Open SWE could "
            "not provision a replacement, so this run had nowhere to work. "
            "Retrigger this thread to try again."
        )
    return warning(
        f"This thread's sandbox{which} stopped responding, and Open SWE can't tell "
        "whether it will come back. Open SWE will not start a replacement on its "
        "own: a new sandbox is empty, so swapping one in would throw away anything "
        "not yet committed and pushed while still looking like a recovery. "
        "Retrigger this thread to try the same sandbox again, or start a new thread "
        "to get a fresh one."
    )


_SANDBOX_ID_RE = re.compile(r"\bsb-[A-Za-z0-9-]+\b")


def extract_sandbox_id(text: str) -> str | None:
    match = _SANDBOX_ID_RE.search(text)
    return match.group(0) if match else None


async def _get_slack_target(configurable: Mapping[str, Any]) -> tuple[str, str] | None:
    slack_thread = configurable.get("slack_thread")
    thread_id = configurable.get("thread_id")
    active = await get_active_slack_thread(
        get_client(url=LANGGRAPH_URL),
        thread_id if isinstance(thread_id, str) else None,
        slack_thread if isinstance(slack_thread, Mapping) else None,
    )
    if not active:
        return None
    channel_id = active.get("channel_id")
    thread_ts = active.get("thread_ts")
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


async def post_sandbox_unreachable_notification(
    config: Mapping[str, Any],
    *,
    sandbox_id: str | None = None,
    sandbox_name: str | None = None,
    replacement_attempted: bool = False,
) -> None:
    configurable = config.get("configurable", {})
    if not isinstance(configurable, Mapping):
        logger.info("No runtime configurable found for sandbox unreachable notification")
        return

    message = sandbox_unreachable_message(
        sandbox_id=sandbox_id,
        sandbox_name=sandbox_name,
        replacement_attempted=replacement_attempted,
    )

    slack_target = await _get_slack_target(configurable)
    if slack_target is not None:
        channel_id, thread_ts = slack_target
        thread_id = configurable.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            await post_slack_thread_reply(channel_id, thread_ts, message, agent_thread_id=thread_id)
        else:
            await post_slack_thread_reply(channel_id, thread_ts, message)
        logger.info("Sent sandbox unreachable notification to Slack thread %s", thread_ts)
        return

    linear_issue_id = _get_linear_issue_id(configurable)
    if linear_issue_id is not None:
        await comment_on_linear_issue(linear_issue_id, message)
        logger.info("Sent sandbox unreachable notification to Linear issue %s", linear_issue_id)
        return

    github_target = _get_github_target(configurable)
    if github_target is not None:
        token = get_github_token(config) or await get_github_app_installation_token()
        if not token:
            logger.info("No GitHub token available for sandbox unreachable notification")
            return
        repo, issue_number = github_target
        await post_github_comment(repo, issue_number, message, token=token)
        logger.info("Sent sandbox unreachable notification to GitHub item #%s", issue_number)
        return

    logger.info("No user-facing target found for sandbox unreachable notification")
