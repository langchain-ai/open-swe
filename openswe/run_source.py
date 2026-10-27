"""Telling the person on the channel a run was started from: Slack, Linear or GitHub."""

import logging
from collections.abc import Mapping

from langgraph_sdk import get_client

from openswe.github.app import get_github_app_installation_token
from openswe.github.comments import post_github_comment
from openswe.github.thread_token import resolve_thread_github_token
from openswe.linear.notifications import post_linear_notification
from openswe.run_config import RunConfig
from openswe.slack.client import (
    LANGGRAPH_URL,
    get_active_slack_thread,
    post_slack_thread_reply,
)

logger = logging.getLogger(__name__)


async def _slack_target(cfg: RunConfig) -> tuple[str, str] | None:
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


async def notify_run_source(config: Mapping[str, object], message: str, *, what: str) -> None:
    """Post ``message`` where the run was started; ``what`` names it in the logs."""
    cfg = RunConfig.from_config(config)

    slack_target = await _slack_target(cfg)
    if slack_target is not None:
        channel_id, thread_ts = slack_target
        if cfg.thread_id:
            await post_slack_thread_reply(
                channel_id, thread_ts, message, agent_thread_id=cfg.thread_id
            )
        else:
            await post_slack_thread_reply(channel_id, thread_ts, message)
        logger.info("Sent a run notification to Slack", extra={"notification": what})
        return

    if cfg.linear_issue and cfg.linear_issue.id:
        await post_linear_notification(cfg.linear_issue.id, message)
        return

    github_target = _get_github_target(cfg)
    if github_target is not None:
        token = (
            await resolve_thread_github_token(config) or await get_github_app_installation_token()
        )
        if not token:
            logger.info("No GitHub token for a run notification", extra={"notification": what})
            return
        repo, issue_number = github_target
        await post_github_comment(repo, issue_number, message, token=token)
        logger.info(
            "Sent a run notification to GitHub",
            extra={"notification": what, "issue_number": issue_number},
        )
        return

    logger.info("No user-facing target for a run notification", extra={"notification": what})
