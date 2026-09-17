"""Tool that nominates a tiny pull request for approval from Slack."""

from collections.abc import Mapping
from typing import Any, Literal

from langgraph.config import get_config
from langgraph_sdk import get_client

from agent.dashboard.workspace_settings import get_workspace_settings
from agent.expedited_review.approvals import ExpeditedApproval
from agent.expedited_review.eligibility import (
    MAX_CHANGED_LINES,
    Ineligible,
    assess_eligibility,
    fetch_changed_files,
)
from agent.expedited_review.watch import evaluate_approval, retire, start_approval
from agent.github.ci import fetch_pr
from agent.github.pull_requests import PullRequest, PullRequestPayload
from agent.github.token import resolve_github_token
from agent.prompts import render_prompt
from agent.run_config import RunConfig
from agent.slack.blocks import escape
from agent.slack.client import (
    GitHubPrRef,
    get_active_slack_thread,
    join_slack_channel,
    parse_github_pr_url,
    post_slack_top_level_message_with_ts,
    resolve_slack_channel_id,
)
from agent.tools.manage_baby_sit import dispatch_run_config


def _failure(error: str) -> dict[str, Any]:
    return {"success": False, "error": error}


async def _context_location(cfg: RunConfig, thread_id: str) -> tuple[str, str]:
    """The run's own Slack ``(channel_id, thread_ts)``; either may be empty."""
    slack_thread = await get_active_slack_thread(
        get_client(), thread_id, cfg.slack_thread.dump() if cfg.slack_thread else None
    )
    channel_id = str((slack_thread or {}).get("channel_id") or "")
    thread_ts = str((slack_thread or {}).get("thread_ts") or "")
    if not channel_id and cfg.slack_thread is not None:
        channel_id = cfg.slack_thread.channel_id.strip()
    return channel_id, thread_ts


async def _post_root_message(
    channel_id: str, pr_ref: GitHubPrRef, title: str
) -> tuple[str | None, str | None]:
    """Open a thread in ``channel_id`` for the card; joins the channel if needed."""
    text = render_prompt(
        "slack/expedited-review-requested.md",
        pr_url=pr_ref.url,
        label=f"{pr_ref.owner}/{pr_ref.repo}#{pr_ref.number}",
        title=escape(title),
    )
    message_ts, error = await post_slack_top_level_message_with_ts(
        channel_id, text, unfurl_links=False, unfurl_media=False
    )
    if message_ts is None and error == "not_in_channel" and await join_slack_channel(channel_id):
        message_ts, error = await post_slack_top_level_message_with_ts(
            channel_id, text, unfurl_links=False, unfurl_media=False
        )
    return message_ts, error


async def expedite_pr_approval(
    pr_url: str, action: Literal["start", "cancel"] = "start", channel: str = ""
) -> dict[str, Any]:
    """Implement the `expedite_pr_approval` tool."""
    pr_ref = parse_github_pr_url(pr_url)
    if pr_ref is None:
        return _failure("pr_url must be a canonical GitHub pull request URL")
    config = get_config()
    cfg = RunConfig.from_config(config)
    thread_id = cfg.thread_id
    if not thread_id:
        return _failure("No executable agent thread is available")
    if not (await get_workspace_settings()).expedited_review_enabled:
        return _failure(
            "Expedited review is disabled for this Open SWE instance. "
            "An admin can enable it under Settings; ask for a normal review instead."
        )

    if action == "cancel":
        approval = await ExpeditedApproval.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
        if approval is None:
            return {"success": True, "cancelled": False}
        if approval.thread_id and approval.thread_id != thread_id:
            return _failure("This expedited review belongs to another agent thread")
        await retire(approval, "failed", "Cancelled by the agent.")
        return {"success": True, "cancelled": True}

    channel_id, thread_ts = await _context_location(cfg, thread_id)
    if channel.strip():
        requested = await resolve_slack_channel_id(channel)
        if requested is None:
            return _failure(
                f"Slack channel {channel.strip()!r} was not found. Pass a channel name the "
                "bot can see or a channel id."
            )
        if requested != channel_id:
            channel_id, thread_ts = requested, ""
    if not channel_id:
        return _failure(
            "Expedited review posts its approval card in Slack. This thread has no Slack "
            "location, so pass `channel` with the Slack channel name or id to post in."
        )

    try:
        token, _ = await resolve_github_token(
            config if isinstance(config, Mapping) else {}, thread_id
        )
    except Exception as exc:
        return _failure(f"GitHub authentication failed: {exc}")
    pr = await fetch_pr(owner=pr_ref.owner, repo=pr_ref.repo, pr_number=pr_ref.number, token=token)
    if not pr:
        return _failure("Pull request is unavailable")
    if pr.get("state") != "open":
        return _failure("Pull request is not open")
    if pr.get("draft") is True:
        return _failure("Pull request is a draft; mark it ready for review first")
    head = pr.get("head") if isinstance(pr.get("head"), Mapping) else {}
    head_sha = head.get("sha") if isinstance(head, Mapping) else None
    if not isinstance(head_sha, str) or not head_sha:
        return _failure("Pull request head SHA is unavailable")

    files = await fetch_changed_files(
        owner=pr_ref.owner, repo=pr_ref.repo, pr_number=pr_ref.number, token=token
    )
    if files is None:
        return _failure("Could not read the pull request's changed files")
    verdict = assess_eligibility(files)
    if isinstance(verdict, Ineligible):
        return _failure(
            f"Not eligible for expedited review: {verdict.reason}. "
            f"Eligible changes touch at most {MAX_CHANGED_LINES} lines and every changed "
            "file has to have a readable text diff. Ask for a normal review."
        )

    payload = PullRequestPayload.model_validate(pr)
    if not thread_ts:
        thread_ts, error = await _post_root_message(channel_id, pr_ref, payload.title)
        if not thread_ts:
            return _failure(
                f"Could not post in Slack channel {channel_id}: {error or 'unknown error'}. "
                "For a private channel, invite the bot first."
            )

    pull_request = await PullRequest.load(pr_ref.owner, pr_ref.repo, pr_ref.number)
    if not pull_request.title:
        pull_request.title = payload.title
        pull_request.head_ref = payload.head_ref
        pull_request.base_ref = payload.base_ref
        pull_request.author = payload.author
        pull_request.author_github_id = payload.author_id
    pull_request = await pull_request.link_thread(thread_id, source="expedited_review")
    approval = await start_approval(
        pull_request=pull_request,
        thread_id=thread_id,
        head_sha=head_sha,
        diff_fingerprint=verdict.fingerprint,
        slack_channel_id=channel_id,
        slack_thread_ts=thread_ts,
        run_config=dispatch_run_config(cfg, thread_id, None),
    )
    status = await evaluate_approval(str(approval.id))
    return {
        "success": True,
        "approval_id": str(approval.id),
        "pr_url": pr_ref.url,
        "head_sha": head_sha,
        "changed_lines": verdict.changed_lines,
        "slack_channel_id": channel_id,
        "status": status,
        "next": (
            "The approval card is posted in the Slack thread; two approvals merge the PR."
            if status == "open"
            else "Open SWE is watching the PR and will post the card once checks and reviews "
            "are clean. Do not poll; you will be told if it is rejected or withdrawn."
        ),
    }
