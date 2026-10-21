"""Human decisions on workflow pushes, including cards delivered outside their thread."""

import logging

from fastapi import BackgroundTasks, HTTPException

from openswe.prompts import prompt
from openswe.slack.client import post_slack_ephemeral_message, update_slack_message
from openswe.slack.dm import is_dm_channel, note_for_concierge, open_dm
from openswe.slack.payloads import SlackButtonValue, SlackChannelContext, SlackInteraction
from openswe.slack.records import lookup_slack_thread_id
from openswe.slack.responses import WebhookResponse, accepted, ignored
from openswe.tasks.store import TaskMembership
from openswe.threads.plan_api import fetch_thread_metadata
from openswe.threads.summary import assert_thread_postable, thread_is_owner
from openswe.threads.workflow_approval import (
    WORKFLOW_APPROVAL_PENDING,
    WorkflowApprovalAlreadyDecided,
    decide_workflow_push_approval,
    get_workflow_push_approvals,
)
from openswe.threads.workflow_approval_api import dispatch_followup
from openswe.users import User

logger = logging.getLogger(__name__)
BUTTON_TYPE = "workflow_push_approval"


async def handle_button(
    interaction: SlackInteraction,
    button: SlackButtonValue,
    background_tasks: BackgroundTasks,
    *,
    channel_context: SlackChannelContext,
    thread_ts: str,
) -> WebhookResponse:
    channel_id = interaction.channel_id
    user_id = interaction.user.id
    if not channel_id or not user_id or not button.fingerprint:
        return ignored("Missing workflow approval context")
    if button.action not in {"approve", "reject"}:
        return ignored("Unknown workflow approval action")

    async def refuse(reason: str) -> WebhookResponse:
        await post_slack_ephemeral_message(
            channel_id,
            user_id,
            reason,
            thread_ts=interaction.message.thread_ts or interaction.container.thread_ts or None,
        )
        if is_dm_channel(channel_context):
            await note_for_concierge(user_id, channel_id, reason)
        return ignored(reason)

    login = await User.login_for_slack(user_id)
    if not login:
        return await refuse("Link your GitHub account before answering a workflow approval.")
    source_id = await lookup_slack_thread_id(channel_id, thread_ts) if thread_ts else None
    thread_id = button.thread_id or source_id
    if not thread_id:
        return await refuse("This workflow approval is not associated with an agent thread.")
    try:
        metadata = await fetch_thread_metadata(thread_id)
        assert_thread_postable(metadata, login)
    except HTTPException:
        logger.info("Workflow approval target is not promptable", extra={"thread_id": thread_id})
        return await refuse("You cannot answer this workflow approval request.")
    if not thread_is_owner(metadata, login):
        return await refuse("Only the thread owner can answer this workflow approval request.")
    if button.thread_id and thread_id != source_id:
        if is_dm_channel(channel_context):
            eligible = await open_dm(user_id) == channel_id
        else:
            task = await TaskMembership.context_for_thread(thread_id)
            eligible = task is not None and task.task.coordinator_thread_id == source_id
        if not eligible:
            return await refuse("This workflow approval card does not belong in this conversation.")

    record = (await get_workflow_push_approvals(thread_id)).get(button.fingerprint)
    if record is None:
        return await refuse(
            "I couldn't find that workflow approval request. Trigger the push again."
        )
    if record.get("status") != WORKFLOW_APPROVAL_PENDING:
        return await refuse("This workflow approval was already answered; no action was taken.")
    approved = button.action == "approve"
    try:
        record = await decide_workflow_push_approval(
            thread_id, button.fingerprint, approved=approved, actor=login
        )
    except WorkflowApprovalAlreadyDecided:
        return await refuse("This workflow approval was already answered; no action was taken.")
    if record is None:
        return await refuse(
            "I couldn't find that workflow approval request. Trigger the push again."
        )

    verdict = "approved" if approved else "rejected"
    await _close_card(interaction, verdict)
    confirmation = f"Workflow push {verdict} for fingerprint `{button.fingerprint}`. " + (
        "Open SWE will retry the blocked push." if approved else "No workflow files will be pushed."
    )
    await post_slack_ephemeral_message(
        channel_id,
        user_id,
        confirmation,
        thread_ts=interaction.message.thread_ts or interaction.container.thread_ts or None,
    )
    if is_dm_channel(channel_context):
        await note_for_concierge(user_id, channel_id, confirmation)
    if approved:
        background_tasks.add_task(
            dispatch_followup,
            thread_id,
            metadata,
            prompt("runs/workflow-push-approved"),
            github_login=login,
            multitask_strategy="enqueue",
        )
    return accepted(f"Workflow push {verdict}" + (", retry queued" if approved else ""))


async def _close_card(interaction: SlackInteraction, verdict: str) -> None:
    blocks = [block for block in interaction.message.blocks if block.get("type") != "actions"]
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "plain_text", "text": f"Workflow push {verdict}"}],
        }
    )
    try:
        await update_slack_message(
            interaction.channel_id,
            interaction.message_ts,
            interaction.message.text or f"Workflow push {verdict}",
            blocks=blocks,
        )
    except Exception:
        logger.warning("Could not close workflow approval card", exc_info=True)
