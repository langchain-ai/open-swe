"""Telling the user, on the channel they triggered from, that their sandbox went quiet."""

import logging
import re
from collections.abc import Mapping
from typing import Any

from langgraph_sdk import get_client

from openswe.github.app import get_github_app_installation_token
from openswe.github.comments import post_github_comment
from openswe.github.thread_token import resolve_thread_github_token
from openswe.run_config import RunConfig
from openswe.slack.client import (
    LANGGRAPH_URL,
    SANDBOX_UNREACHABLE_NOTICE,
    SlackNotice,
    get_active_slack_thread,
    post_slack_thread_reply,
)
from openswe.utils.user_messages import warning

logger = logging.getLogger(__name__)


def sandbox_unreachable_message(
    *,
    sandbox_id: str | None = None,
    sandbox_name: str | None = None,
    handed_to_mda: bool = False,
    reason: str | None = None,
) -> str:
    """User-facing text for a sandbox that stopped answering.

    Deliberately does not claim the sandbox is gone for good — all we observed is
    that it stopped responding, and it may come back. ``handed_to_mda`` is for a
    sandbox Open SWE set aside (deleted, or a reviewer's that stopped answering), where
    the thread's next run starts in a fresh one and "will not start a replacement"
    would be untrue.
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
    if reason:
        return warning(
            f"Open SWE could not open this thread's sandbox{which}: `{reason.replace('`', "'")}`. "
            "Retrigger this thread to try again."
        )
    if handed_to_mda:
        return warning(
            f"This thread's sandbox{which} is gone or stopped responding, so this run "
            "had nowhere to work. Its next run starts in a fresh sandbox, without "
            "anything that was not pushed. Retrigger this thread to continue."
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


async def get_slack_target(cfg: RunConfig) -> tuple[str, str] | None:
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
    handed_to_mda: bool = False,
    reason: str | None = None,
) -> None:
    cfg = RunConfig.from_config(config)

    message = sandbox_unreachable_message(
        sandbox_id=sandbox_id,
        sandbox_name=sandbox_name,
        handed_to_mda=handed_to_mda,
        reason=reason,
    )

    slack_target = await get_slack_target(cfg)
    if slack_target is not None:
        channel_id, thread_ts = slack_target
        notice = SlackNotice(SANDBOX_UNREACHABLE_NOTICE, message)
        if cfg.thread_id:
            await post_slack_thread_reply(
                channel_id, thread_ts, message, agent_thread_id=cfg.thread_id, notice=notice
            )
        else:
            await post_slack_thread_reply(channel_id, thread_ts, message, notice=notice)
        logger.info("Sent sandbox unreachable notification to Slack thread %s", thread_ts)
        return

    github_target = _get_github_target(cfg)
    if github_target is not None:
        token = (
            await resolve_thread_github_token(config) or await get_github_app_installation_token()
        )
        if not token:
            logger.info("No GitHub token available for sandbox unreachable notification")
            return
        repo, issue_number = github_target
        await post_github_comment(repo, issue_number, message, token=token)
        logger.info("Sent sandbox unreachable notification to GitHub item #%s", issue_number)
        return

    logger.info("No user-facing target found for sandbox unreachable notification")
