"""Deliver workflow approval requests without granting agents the decision."""

import logging
from collections.abc import Mapping
from html import escape

from fastapi import HTTPException

from openswe.prompts import prompt
from openswe.slack.client import post_slack_thread_reply_with_ts
from openswe.slack.dm import DmOrigin, send_dm
from openswe.slack.thread_owner import wake_thread_owner
from openswe.slack.tools.reply import build_workflow_approval_blocks
from openswe.source_context import SourceContext
from openswe.tasks.store import TaskMembership
from openswe.threads.plan_api import fetch_thread_metadata
from openswe.threads.summary import assert_thread_postable, thread_is_owner, thread_is_private
from openswe.threads.workflow_approval import (
    WORKFLOW_APPROVAL_PENDING,
    get_workflow_push_approvals,
    mark_workflow_push_notified,
)
from openswe.users import User
from openswe.utils.dashboard_links import dashboard_workflow_approval_url

logger = logging.getLogger(__name__)


def workflow_approval_message(thread_id: str, record: Mapping[str, object]) -> str:
    files = record.get("files")
    paths = [str(path) for path in files] if isinstance(files, list) else []
    listed = "\n".join(f"• `{escape(path[:150])}`" for path in paths[:10])
    if len(paths) > 10:
        listed += f"\n• …and {len(paths) - 10} more"
    stats = record.get("diff_stats")
    stats = stats if isinstance(stats, Mapping) else {}
    fingerprint = str(record.get("fingerprint") or "")
    url = dashboard_workflow_approval_url(thread_id, fingerprint)
    review = f"\n*Review exact diff:* <{url}|Open in Web>" if url else ""
    inherited = record.get("inherited_from")
    source = (
        f"These workflow changes were inherited from `{escape(str(inherited))}`."
        if inherited
        else "Open SWE is ready to push GitHub Actions workflow changes."
    )
    return (
        f"*Confirm GitHub Actions workflow changes*\n{source}\n"
        f"*Repository:* `{escape(str(record.get('repo') or ''))}`\n"
        f"*Branch:* `{escape(str(record.get('branch') or ''))}`\n\n"
        "*Why confirmation is required:* Workflows control CI jobs and may access repository secrets.\n\n"
        f"{listed}\n\n*Diff stat:* {len(paths)} files, "
        f"+{stats.get('additions', 0)} / -{stats.get('deletions', 0)}\n"
        f"*Approval ID:* `{fingerprint}`{review}\n\n"
        "Only the implementation thread's owner can approve. Approval resumes that exact push; "
        "changed workflows require a new approval."
    )


async def share_workflow_approval(
    thread_id: str,
    fingerprint: str,
    *,
    current_thread_id: str,
    actor_login: str,
) -> dict[str, object]:
    """Present a pending approval in its own Slack thread or its coordinator's."""
    metadata = await fetch_thread_metadata(thread_id)
    assert_thread_postable(metadata, actor_login)
    if not thread_is_owner(metadata, actor_login):
        raise HTTPException(403, "only the implementation thread owner can share this approval")
    if current_thread_id != thread_id:
        membership = await TaskMembership.context_for_thread(thread_id)
        if membership is None or membership.task.coordinator_thread_id != current_thread_id:
            raise HTTPException(403, "approval can only be shared with its task coordinator")
    destination = await fetch_thread_metadata(current_thread_id)
    assert_thread_postable(destination, actor_login)
    if thread_is_private(metadata) and not thread_is_private(destination):
        raise HTTPException(403, "private workflow approvals cannot be shared into public threads")
    location = SourceContext.from_metadata(destination).slack_thread
    if location is None or location.location is None:
        raise HTTPException(409, "current thread has no active Slack location")
    record = (await get_workflow_push_approvals(thread_id)).get(fingerprint)
    if not record or record.get("status") != WORKFLOW_APPROVAL_PENDING:
        raise HTTPException(409, "workflow approval is no longer pending")
    message = workflow_approval_message(thread_id, record)
    await post_slack_thread_reply_with_ts(
        location.channel_id,
        location.thread_ts,
        message,
        blocks=build_workflow_approval_blocks(message, fingerprint, thread_id=thread_id),
        agent_thread_id=current_thread_id,
    )
    await mark_workflow_push_notified(thread_id, fingerprint)
    return {"success": True, "status": "shared", "thread_id": thread_id, "fingerprint": fingerprint}


async def propagate_workflow_approval(thread_id: str, record: Mapping[str, object]) -> bool:
    """Ask the coordinator to present the card, falling back to the owner's DM."""
    metadata = await fetch_thread_metadata(thread_id)
    owner = metadata.get("owner_login")
    if not isinstance(owner, str) or not owner:
        return False
    user = await User.for_login("github", owner)
    if user is None or not user.slack_user_id:
        return False
    fingerprint = str(record.get("fingerprint") or "")
    origin = None
    membership = await TaskMembership.context_for_thread(thread_id)
    if membership and membership.task.coordinator_thread_id != thread_id:
        coordinator_id = membership.task.coordinator_thread_id
        if coordinator_id:
            coordinator = await fetch_thread_metadata(coordinator_id)
            location = SourceContext.from_metadata(coordinator).slack_thread
            if location and location.location and thread_is_owner(coordinator, owner):
                origin = DmOrigin(
                    channel_id=location.channel_id,
                    thread_ts=location.thread_ts,
                    subject="Workflow push approval",
                )
                if not thread_is_private(metadata) or thread_is_private(coordinator):
                    try:
                        await wake_thread_owner(
                            location.channel_id,
                            location.thread_ts,
                            user.slack_user_id,
                            prompt(
                                "slack/workflow-approval-pending",
                                thread_id=thread_id,
                                fingerprint=fingerprint,
                            ),
                        )
                        return True
                    except Exception:
                        logger.warning(
                            "Could not hand workflow approval to the coordinator",
                            extra={"agent_thread_id": thread_id},
                            exc_info=True,
                        )
    message = workflow_approval_message(thread_id, record)
    return await send_dm(
        user.slack_user_id,
        message,
        blocks=build_workflow_approval_blocks(message, fingerprint, thread_id=thread_id),
        origin=origin if not thread_is_private(metadata) else None,
    )
