"""Telling the user, on the channel they triggered from, that their sandbox went quiet."""

import logging
import re
from collections.abc import Mapping
from typing import Any

from langgraph_sdk import get_client

from agent.run_config import RunConfig

from ..utils.github_app import get_github_app_installation_token
from ..utils.github_comments import post_github_comment
from ..utils.github_token import get_github_token
from ..utils.linear import comment_on_linear_issue
from ..utils.slack import LANGGRAPH_URL, get_active_slack_thread, post_slack_thread_reply
from ..utils.user_messages import warning

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


async def _get_slack_target(cfg: RunConfig) -> tuple[str, str] | None:
    active = await get_active_slack_thread(
        get_client(url=LANGGRAPH_URL),
        cfg.thread_id,
        cfg.slack_thread.dump() if cfg.slack_thread else None,
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


def _get_linear_issue_id(cfg: RunConfig) -> str | None:
    return cfg.linear_issue.id or None if cfg.linear_issue else None


def _get_github_target(cfg: RunConfig) -> tuple[dict[str, str], int] | None:
    if not cfg.repo:
        return None
    repo = {"owner": cfg.repo.owner, "name": cfg.repo.name}

    target = cfg.github_pr_or_issue
    if target is not None:
        if target.repo and target.repo.owner and target.repo.name:
            repo = {"owner": target.repo.owner, "name": target.repo.name}
        if target.number is not None:
            return repo, target.number

    if cfg.github_issue is not None and cfg.github_issue.number is not None:
        return repo, cfg.github_issue.number

    if cfg.pr_number is not None:
        return repo, cfg.pr_number
    return None


async def post_sandbox_unreachable_notification(
    config: Mapping[str, Any],
    *,
    sandbox_id: str | None = None,
    sandbox_name: str | None = None,
    replacement_attempted: bool = False,
) -> None:
    cfg = RunConfig.from_config(config)

    message = sandbox_unreachable_message(
        sandbox_id=sandbox_id,
        sandbox_name=sandbox_name,
        replacement_attempted=replacement_attempted,
    )

    slack_target = await _get_slack_target(cfg)
    if slack_target is not None:
        channel_id, thread_ts = slack_target
        if cfg.thread_id:
            await post_slack_thread_reply(
                channel_id, thread_ts, message, agent_thread_id=cfg.thread_id
            )
        else:
            await post_slack_thread_reply(channel_id, thread_ts, message)
        logger.info("Sent sandbox unreachable notification to Slack thread %s", thread_ts)
        return

    linear_issue_id = _get_linear_issue_id(cfg)
    if linear_issue_id is not None:
        await comment_on_linear_issue(linear_issue_id, message)
        logger.info("Sent sandbox unreachable notification to Linear issue %s", linear_issue_id)
        return

    github_target = _get_github_target(cfg)
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
