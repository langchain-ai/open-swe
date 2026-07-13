"""Slack webhook HTTP routes."""

from fastapi import APIRouter

from . import common
from . import slack as service

router = APIRouter()


@router.post("/webhooks/slack")
async def slack_webhook(
    request: common.Request, background_tasks: common.BackgroundTasks
) -> dict[str, str]:
    """Handle Slack Event API webhooks for app mentions."""
    body = await request.body()

    signature = request.headers.get("X-Slack-Signature", "")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    if not common.verify_slack_signature(
        body=body,
        timestamp=timestamp,
        signature=signature,
        secret=common.SLACK_SIGNING_SECRET,
    ):
        common.logger.warning("Invalid Slack signature")
        raise common.HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = common.json.loads(body)
    except common.json.JSONDecodeError:
        common.logger.exception("Failed to parse Slack webhook JSON")
        return {"status": "error", "message": "Invalid JSON"}

    if payload.get("type") == "url_verification":
        challenge = payload.get("challenge", "")
        return {"challenge": challenge}

    if payload.get("type") != "event_callback":
        return {"status": "ignored", "reason": "Not an event callback"}

    event = payload.get("event", {})

    if event.get("type") == "reaction_added":
        reaction = event.get("reaction")
        if reaction in common.FEEDBACK_REACTIONS:
            background_tasks.add_task(
                common.process_slack_reaction_added, event, payload.get("event_id", "")
            )
            return {"status": "accepted", "message": "Reaction feedback queued"}
        return {"status": "ignored", "reason": "Reaction not tracked for feedback"}

    if event.get("type") == "reaction_removed":
        reaction = event.get("reaction")
        if reaction in common.FEEDBACK_REACTIONS:
            background_tasks.add_task(
                common.process_slack_reaction_removed, event, payload.get("event_id", "")
            )
            return {"status": "accepted", "message": "Reaction removal queued"}
        return {"status": "ignored", "reason": "Reaction not tracked for feedback"}

    bot_user_id = common.SLACK_BOT_USER_ID
    if not bot_user_id:
        authorizations = payload.get("authorizations", [])
        if isinstance(authorizations, list) and authorizations:
            auth_user_id = authorizations[0].get("user_id")
            if isinstance(auth_user_id, str):
                bot_user_id = auth_user_id
    if not bot_user_id:
        authed_users = payload.get("authed_users", [])
        if isinstance(authed_users, list) and authed_users:
            first_user = authed_users[0]
            if isinstance(first_user, str):
                bot_user_id = first_user

    is_direct_message = (
        event.get("type") == "message"
        and event.get("channel_type") == "im"
        and bool(event.get("user"))
    )
    if event.get("type") != "app_mention":
        message_text = event.get("text", "")
        has_username_mention = bool(
            event.get("type") == "message"
            and common.SLACK_BOT_USERNAME
            and f"@{common.SLACK_BOT_USERNAME}" in message_text
        )
        has_id_mention = bool(
            event.get("type") == "message"
            and common.SLACK_BOT_USER_ID
            and f"<@{common.SLACK_BOT_USER_ID}>" in message_text
        )
        is_ready_plan_reply = bool(
            event.get("type") == "message"
            and not is_direct_message
            and await service._slack_user_can_reply_to_ready_plan(
                str(event.get("channel") or ""),
                str(event.get("thread_ts") or ""),
                str(event.get("user") or ""),
            )
        )
        is_untagged_two_party_reply = bool(
            event.get("type") == "message"
            and not event.get("subtype")
            and await service._slack_thread_allows_untagged_reply(
                str(event.get("channel") or ""),
                str(event.get("thread_ts") or ""),
                message_text,
                bot_user_id,
            )
        )
        should_handle_message = any(
            (
                has_username_mention,
                has_id_mention,
                is_ready_plan_reply,
                is_untagged_two_party_reply,
                is_direct_message,
            )
        )
        if not should_handle_message:
            return {"status": "ignored", "reason": "Not an app mention, DM, or plan reply"}

    if event.get("subtype") == "bot_message" or event.get("bot_id"):
        return {"status": "ignored", "reason": "Event from a bot"}

    channel_id = event.get("channel", "")
    event_ts = event.get("ts", "")
    thread_ts = event.get("thread_ts") or event_ts
    user_id = event.get("user", "")
    text = event.get("text", "")
    if not channel_id or not event_ts or not thread_ts:
        return {"status": "ignored", "reason": "Missing channel/thread timestamp"}

    if bot_user_id and user_id == bot_user_id:
        return {"status": "ignored", "reason": "Event from this bot user"}

    channel_context = await common._get_slack_channel_context(channel_id)

    if await common._is_docs_plz_slack_channel(channel_id, channel_context):
        background_tasks.add_task(
            common.post_slack_thread_reply,
            channel_id,
            thread_ts,
            common.DOCS_PLZ_SLACK_GATE_REPLY,
        )
        return {"status": "accepted", "message": "Slack mention gated for docs-plz"}

    event_data = {
        "channel_id": channel_id,
        "channel_context": channel_context,
        "thread_ts": thread_ts,
        "event_ts": event_ts,
        "user_id": user_id,
        "text": text,
        "bot_user_id": bot_user_id,
        "treat_all_messages_as_mentions": is_direct_message,
    }
    repo_config = await common.get_slack_repo_config(
        channel_id, thread_ts, slack_user_id=user_id, channel_context=channel_context
    )

    background_tasks.add_task(service.process_slack_mention, event_data, repo_config)

    return {"status": "accepted", "message": "Slack mention queued"}


@router.post("/webhooks/slack/interactivity")
async def slack_interactivity(
    request: common.Request, background_tasks: common.BackgroundTasks
) -> dict[str, str]:
    """Handle Slack Block Kit interactions."""
    body = await request.body()
    signature = request.headers.get("X-Slack-Signature", "")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    if not common.verify_slack_signature(
        body=body,
        timestamp=timestamp,
        signature=signature,
        secret=common.SLACK_SIGNING_SECRET,
    ):
        common.logger.warning("Invalid Slack interactivity signature")
        raise common.HTTPException(status_code=401, detail="Invalid signature")

    form = common.parse_qs(body.decode("utf-8"))
    payload_raw = (form.get("payload") or [""])[0]
    try:
        payload = common.json.loads(payload_raw)
    except common.json.JSONDecodeError:
        common.logger.exception("Failed to parse Slack interactivity payload")
        return {"status": "error", "message": "Invalid payload"}

    action = _first_open_swe_option_action(payload.get("actions"))
    if action is None:
        return {"status": "ignored", "reason": "No Open SWE action"}

    try:
        action_value = common.json.loads(str(action.get("value") or "{}"))
    except common.json.JSONDecodeError:
        return {"status": "ignored", "reason": "Invalid action value"}
    if action_value.get("type") == "workflow_push_approval":
        workflow_action = str(action_value.get("action") or "").strip()
        fingerprint = str(action_value.get("fingerprint") or "").strip()
        channel = payload.get("channel") if isinstance(payload.get("channel"), dict) else {}
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        container = payload.get("container") if isinstance(payload.get("container"), dict) else {}
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        channel_id = str(channel.get("id") or container.get("channel_id") or "")
        thread_ts = str(
            message.get("thread_ts") or message.get("ts") or container.get("thread_ts") or ""
        )
        user_id = str(user.get("id") or "")
        if not channel_id or not thread_ts or not fingerprint:
            return {"status": "ignored", "reason": "Missing workflow approval context"}

        thread_id = common.generate_thread_id_from_slack_thread(channel_id, thread_ts)
        if not await common._slack_user_is_thread_owner(thread_id, user_id):
            await common.post_slack_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text="Only the person who requested this run can approve workflow file pushes.",
            )
            return {"status": "ignored", "reason": "approver is not the thread owner"}

        if workflow_action not in {"approve", "reject"}:
            return {"status": "ignored", "reason": "Unknown workflow approval action"}
        approved = workflow_action == "approve"
        record = await common.decide_workflow_push_approval(
            thread_id, fingerprint, approved=approved, actor=user_id
        )
        if record is None:
            await common.post_slack_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text="I couldn't find that workflow approval request. Trigger the push again to create a fresh approval.",
            )
            return {"status": "ignored", "reason": "workflow approval not found"}
        if not approved:
            await common.post_slack_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=f"Workflow push rejected for fingerprint `{fingerprint}`. No workflow files will be pushed.",
            )
            return {"status": "accepted", "message": "Workflow push rejected"}

        await common.post_slack_thread_reply(
            channel_id=channel_id,
            thread_ts=thread_ts,
            text=f"Workflow push approved for fingerprint `{fingerprint}`. Open SWE will retry the blocked push.",
        )
        channel_context = await common._get_slack_channel_context(channel_id)
        repo_config = await common.get_slack_repo_config(
            channel_id, thread_ts, slack_user_id=user_id, channel_context=channel_context
        )
        background_tasks.add_task(
            service.process_slack_mention,
            {
                "channel_id": channel_id,
                "channel_context": channel_context,
                "thread_ts": thread_ts,
                "event_ts": str(message.get("ts") or ""),
                "user_id": user_id,
                "text": (
                    "The workflow-file push approval was approved. Retry the blocked "
                    "git push now; do not alter workflow files before pushing."
                ),
                "bot_user_id": common.SLACK_BOT_USER_ID,
            },
            repo_config,
        )
        return {"status": "accepted", "message": "Workflow push approved, retry queued"}

    if action_value.get("type") == "plan_approval":
        plan_action = str(action_value.get("action") or "").strip()
        channel = payload.get("channel") if isinstance(payload.get("channel"), dict) else {}
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        container = payload.get("container") if isinstance(payload.get("container"), dict) else {}
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        channel_id = str(channel.get("id") or container.get("channel_id") or "")
        thread_ts = str(
            message.get("thread_ts") or message.get("ts") or container.get("thread_ts") or ""
        )
        user_id = str(user.get("id") or "")
        if not channel_id or not thread_ts:
            return {"status": "ignored", "reason": "Missing Slack action context"}

        thread_id = common.generate_thread_id_from_slack_thread(channel_id, thread_ts)

        if plan_action == "cancel":
            await common.post_slack_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text="Plan cancelled. No changes will be made.",
            )
            return {"status": "accepted", "message": "Plan cancelled"}

        if plan_action == "approve":
            if not await common._slack_user_is_thread_owner(thread_id, user_id):
                await common.post_slack_thread_reply(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    text="Only the person who requested this plan can approve it. Anyone can reply with feedback or use *Revise Plan*.",
                )
                return {"status": "ignored", "reason": "approver is not the thread owner"}
            await common._set_thread_plan_mode(thread_id, False)
            channel_context = await common._get_slack_channel_context(channel_id)
            repo_config = await common.get_slack_repo_config(
                channel_id, thread_ts, slack_user_id=user_id, channel_context=channel_context
            )
            background_tasks.add_task(
                service.process_slack_mention,
                {
                    "channel_id": channel_id,
                    "channel_context": channel_context,
                    "thread_ts": thread_ts,
                    "event_ts": str(message.get("ts") or ""),
                    "user_id": user_id,
                    "text": "Proceed with the approved plan. Implement the changes as described in the plan.",
                    "bot_user_id": common.SLACK_BOT_USER_ID,
                },
                repo_config,
            )
            return {"status": "accepted", "message": "Plan approved, starting implementation"}

        return {"status": "accepted", "message": "Reply to revise the plan"}

    if action_value.get("type") != "open_swe_option":
        return {"status": "ignored", "reason": "Unknown action type"}

    response = str(action_value.get("response") or "").strip()
    if not response:
        return {"status": "ignored", "reason": "Empty response"}

    channel = payload.get("channel") if isinstance(payload.get("channel"), dict) else {}
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    container = payload.get("container") if isinstance(payload.get("container"), dict) else {}
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    channel_id = str(channel.get("id") or container.get("channel_id") or "")
    event_ts = str(
        action.get("action_ts") or message.get("ts") or container.get("message_ts") or ""
    )
    thread_ts = str(
        message.get("thread_ts") or message.get("ts") or container.get("thread_ts") or event_ts
    )
    user_id = str(user.get("id") or "")
    if not channel_id or not thread_ts or not event_ts or not user_id:
        return {"status": "ignored", "reason": "Missing Slack action context"}

    channel_context = await common._get_slack_channel_context(channel_id)
    repo_config = await common.get_slack_repo_config(
        channel_id, thread_ts, slack_user_id=user_id, channel_context=channel_context
    )
    background_tasks.add_task(
        service.process_slack_mention,
        {
            "channel_id": channel_id,
            "channel_context": channel_context,
            "thread_ts": thread_ts,
            "event_ts": event_ts,
            "user_id": user_id,
            "text": response,
            "bot_user_id": common.SLACK_BOT_USER_ID,
        },
        repo_config,
    )
    return {"status": "accepted", "message": "Slack option queued"}


def _first_open_swe_option_action(actions: common.Any) -> dict[str, common.Any] | None:
    if not isinstance(actions, list):
        return None
    for action in actions:
        if isinstance(action, dict) and action.get("action_id") == "open_swe_option_select":
            return action
    return None


@router.get("/webhooks/slack")
async def slack_webhook_verify() -> dict[str, str]:
    """Verify endpoint for Slack webhook setup."""
    return {"status": "ok", "message": "Slack webhook endpoint is active"}
