"""Tool that posts a Slack approval card for a tiny pull request."""

from collections.abc import Mapping
from typing import Any, Literal

from fastapi import HTTPException
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
from agent.expedited_review.lifecycle import post_card, retire
from agent.github.ci import fetch_pr
from agent.github.pull_request_actions import MarkReadyAction, act_on_pull_request
from agent.github.pull_requests import PullRequest, PullRequestPayload
from agent.github.token import resolve_github_token
from agent.prompts import render_prompt
from agent.run_config import RunConfig
from agent.slack.blocks import escape
from agent.slack.channels import SlackChannel
from agent.slack.client import (
    GitHubPrRef,
    get_active_slack_thread,
    parse_github_pr_url,
    post_slack_top_level_message_with_ts,
)
from agent.tools.manage_baby_sit import dispatch_run_config


def _failure(error: str) -> dict[str, Any]:
    return {"success": False, "error": error}


def _next_step(*, reused: bool, elsewhere: bool, in_thread: bool) -> str:
    """What the agent should do once the card is up."""
    if elsewhere:
        return (
            "This diff already has a card, so it stays in the Slack thread named here, not "
            'the channel you asked for. Cancel with action="cancel" and ask again to move it.'
        )
    posted = (
        "This diff already has an open card; no new one was posted."
        if reused
        else "The approval card is posted in the Slack thread."
    )
    posted += (
        " Clicks only record votes. Call `merge_expedited_pr` once checks and reviews are "
        "clean; keep a `/baby-sit` watch on the PR so you are woken when they are. You are "
        "also woken when the card reaches two approvals or is rejected. Do not poll."
    )
    if reused or not in_thread:
        return posted
    return (
        f"{posted} The card is this turn's reply to the person who asked: finish with "
        "`slack_no_reply_needed` rather than a message announcing the pull request or "
        "this request."
    )


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
    channel: SlackChannel, pr_ref: GitHubPrRef, title: str
) -> tuple[str | None, str | None]:
    """Open a thread in ``channel`` for the card, joining it when the bot is outside."""
    text = render_prompt(
        "slack/expedited-review-requested.md",
        pr_url=pr_ref.url,
        label=f"{pr_ref.owner}/{pr_ref.repo}#{pr_ref.number}",
        title=escape(title),
    )
    message_ts, error = await post_slack_top_level_message_with_ts(
        channel.id, text, unfurl_links=False, unfurl_media=False
    )
    if message_ts is None and error == "not_in_channel" and await channel.join():
        message_ts, error = await post_slack_top_level_message_with_ts(
            channel.id, text, unfurl_links=False, unfurl_media=False
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
        await retire(approval, "cancelled", "Cancelled by the agent.")
        return {"success": True, "cancelled": True}

    own_channel, own_thread = await _context_location(cfg, thread_id)
    channel_id, thread_ts = own_channel, own_thread
    target: SlackChannel | None = None
    if channel.strip():
        target = await SlackChannel.resolve(channel)
        if target is None:
            return _failure(
                f"Slack channel {channel.strip()!r} was not found. Pass a channel name the "
                "bot can see or a channel id."
            )
        # Naming the channel this run already talks in keeps the card in the live
        # thread; a second root there would strand it from the conversation.
        if target.id != own_channel or not own_thread:
            channel_id, thread_ts = target.id, ""
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
        try:
            await act_on_pull_request(
                pr_ref.owner,
                pr_ref.repo,
                pr_ref.number,
                MarkReadyAction(action="mark-ready"),
                token,
            )
        except HTTPException as exc:
            return _failure(f"Pull request is a draft and could not be marked ready: {exc.detail}")
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
            f"Eligible changes touch at most {MAX_CHANGED_LINES} lines outside tests, and "
            "every one of those files has to have a readable text diff. Test files are "
            "not counted. Ask for a normal review."
        )

    payload = PullRequestPayload.model_validate(pr)
    active = await ExpeditedApproval.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
    if active is not None and active.thread_id and active.thread_id != thread_id:
        return _failure("This pull request's expedited review belongs to another agent thread")
    if active is not None and active.diff_fingerprint == verdict.fingerprint:
        return {
            "success": True,
            "approval_id": str(active.id),
            "pr_url": pr_ref.url,
            "head_sha": head_sha,
            "approvers": active.approvers,
            "slack_channel_id": active.slack_channel_id,
            "next": _next_step(
                reused=True,
                elsewhere=active.slack_channel_id != channel_id,
                in_thread=False,
            ),
        }
    if active is not None:
        await retire(active, "superseded", "Replaced by a card for the newer diff.")

    if not thread_ts:
        target = target or await SlackChannel.load(channel_id)
        if target is None:
            return _failure(f"Slack channel {channel_id} is unavailable")
        thread_ts, error = await _post_root_message(target, pr_ref, payload.title)
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
    approval = await ExpeditedApproval(
        pull_request_id=pull_request.id,
        thread_id=thread_id,
        head_sha=head_sha,
        diff_fingerprint=verdict.fingerprint,
        slack_channel_id=channel_id,
        slack_thread_ts=thread_ts,
        run_config=dispatch_run_config(cfg, thread_id, None),
    ).save()
    message_ts, error = await post_card(approval, title=payload.title, files=files)
    if not message_ts:
        async with ExpeditedApproval.locked(approval.id) as (session, row):
            if row is not None:
                await session.delete(row)
        return _failure(f"Could not post the approval card in Slack: {error or 'unknown error'}")
    approval.slack_message_ts = message_ts
    approval = await approval.save()
    return {
        "success": True,
        "approval_id": str(approval.id),
        "pr_url": pr_ref.url,
        "head_sha": head_sha,
        "changed_lines": verdict.changed_lines,
        "test_lines": verdict.test_lines,
        "slack_channel_id": channel_id,
        "next": _next_step(
            reused=False,
            elsewhere=False,
            in_thread=bool(own_thread) and (channel_id, thread_ts) == (own_channel, own_thread),
        ),
    }
