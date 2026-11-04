"""Tool that posts a Slack approval card for a tiny pull request."""

from collections.abc import Mapping
from typing import Any, Literal

from fastapi import HTTPException
from langgraph.config import get_config

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard import repo_access
from openswe.dashboard.workspace_settings import get_workspace_settings
from openswe.expedited_review.eligibility import Exclusion
from openswe.expedited_review.nomination import ExpeditedReviewIneligible, ExpeditedReviewNomination
from openswe.github.token import resolve_github_token
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.requests import HumanReviewRequest
from openswe.human_review.standard import Origin
from openswe.run_config import RunConfig
from openswe.slack.cards import run_slack_location
from openswe.slack.channels import SlackChannel
from openswe.slack.client import parse_github_pr_url
from openswe.tools.manage_baby_sit import dispatch_run_config
from openswe.tools.mcp_exposure import expose_mcp
from openswe.users import User


def _failure(error: str) -> dict[str, Any]:
    return {"success": False, "error": error}


@expose_mcp()
@audit_tool()
async def expedite_pr_approval(
    pr_url: str,
    action: Literal["start", "cancel"] = "start",
    channel: str = "",
    inline_summary: str = "",
    excluded: list[Exclusion] | None = None,
) -> dict[str, Any]:
    """Implement the `expedite_pr_approval` tool."""
    pr_ref = parse_github_pr_url(pr_url)
    if pr_ref is None:
        return _failure("pr_url must be a canonical GitHub pull request URL")
    config = get_config()
    cfg = RunConfig.from_config(config)
    thread_id = cfg.thread_id or ""
    caller = cfg.mcp_caller
    requester = await User.for_login("github", caller) if caller else None
    if not thread_id and requester is None:
        return _failure("No executable agent thread is available")
    if not (await get_workspace_settings()).expedited_review_enabled:
        return _failure(
            "Expedited review is disabled for this Open SWE instance. "
            "An admin can enable it under Settings; ask for a normal review instead."
        )

    def owned_elsewhere(approval: HumanReviewRequest) -> bool:
        if requester is not None:
            return approval.requested_by_user_id != requester.id
        return bool(approval.thread_id) and approval.thread_id != thread_id

    if action == "cancel":
        approval = await HumanReviewRequest.active_for(pr_ref.owner, pr_ref.repo, pr_ref.number)
        if approval is None or approval.kind != "expedited":
            return {"success": True, "cancelled": False}
        if owned_elsewhere(approval):
            return _failure("This expedited review belongs to someone else")
        await ReviewCard(approval).retire("cancelled", "cancelled by the agent")
        return {"success": True, "cancelled": True}

    own_channel, own_thread = await run_slack_location(cfg, thread_id) if thread_id else ("", "")
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
        if caller:
            token = await repo_access.require_repo_access_for_user(
                caller, f"{pr_ref.owner}/{pr_ref.repo}"
            )
        else:
            token, _ = await resolve_github_token(
                config if isinstance(config, Mapping) else {}, thread_id
            )
    except HTTPException as exc:
        return _failure(f"You cannot access {pr_ref.owner}/{pr_ref.repo}: {exc.detail}")
    except Exception as exc:
        return _failure(f"GitHub authentication failed: {exc}")
    origin = Origin(
        requester=requester,
        thread_id=thread_id,
        run_config=dispatch_run_config(cfg, thread_id, None) if thread_id else {},
        slack_channel_id=own_channel,
        slack_thread_ts=own_thread,
    )
    try:
        return await ExpeditedReviewNomination(pr_ref, origin, token).start(
            channel_id, thread_ts, inline_summary, excluded
        )
    except ExpeditedReviewIneligible as exc:
        return _failure(str(exc))
