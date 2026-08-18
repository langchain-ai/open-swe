"""Slack webhook HTTP routes."""

import asyncio
from typing import Any

from fastapi import APIRouter
from langgraph_sdk.client import LangGraphClient

from . import common
from . import slack as service

router = APIRouter()

_MESSAGE_UPDATE_RETRY_DELAYS = (0.1, 0.2, 0.5, 1, 2, 4, 8, 14)


async def _lookup_delivered_message_update(
    langgraph_client: LangGraphClient,
    channel_id: str,
    thread_ts: str,
    message_ts: str,
    user_id: str,
) -> tuple[str | None, dict[str, Any] | None]:
    for delay in (*_MESSAGE_UPDATE_RETRY_DELAYS, None):
        try:
            thread_id = await common.lookup_slack_thread_id(langgraph_client, channel_id, thread_ts)
        except common.SlackThreadMappingError:
            return None, None
        delivered_message = await common.lookup_slack_run_mapping(
            langgraph_client, channel_id, message_ts
        )
        if thread_id and delivered_message:
            if (
                delivered_message.get("thread_ts") != thread_ts
                or delivered_message.get("triggering_user_id") != user_id
                or delivered_message.get("agent_thread_id") != thread_id
            ):
                return None, None
            if await common._thread_exists(thread_id):
                return thread_id, delivered_message
        if delay is None:
            break
        await asyncio.sleep(delay)
    return None, None


async def _process_slack_message_update(
    event_data: dict[str, Any],
    channel_id: str,
    thread_ts: str,
    message_ts: str,
    user_id: str,
) -> None:
    langgraph_client = common.get_client(url=common.LANGGRAPH_URL)
    thread_id, delivered_message = await _lookup_delivered_message_update(
        langgraph_client,
        channel_id,
        thread_ts,
        message_ts,
        user_id,
    )
    if not thread_id or not delivered_message:
        common.logger.info(
            "Ignoring undelivered Slack message update channel=%s message=%s",
            channel_id,
            message_ts,
        )
        return
    event_data["thread_id"] = thread_id
    channel_context = await common._get_slack_channel_context(channel_id)
    event_data["channel_context"] = channel_context
    repo_config = await common.get_slack_repo_config(
        channel_id,
        thread_ts,
        slack_user_id=user_id,
        channel_context=channel_context,
        thread_id=thread_id,
    )
    await service.process_slack_mention(event_data, repo_config)


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
        if reaction == "x":
            background_tasks.add_task(
                common.process_slack_stop_reaction, event, payload.get("event_id", "")
            )
            return {"status": "accepted", "message": "Stop reaction queued"}
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

    event_id = str(payload.get("event_id") or "")
    retry_num = request.headers.get("X-Slack-Retry-Num", "")
    if retry_num and await common.slack_event_already_seen(event_id):
        common.logger.info(
            "Ignoring Slack retry %s of already-handled event %s", retry_num, event_id
        )
        return {"status": "ignored", "reason": "Duplicate Slack event delivery"}

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

    is_message_update = event.get("type") == "message" and event.get("subtype") == "message_changed"
    updated_message = event.get("message") if is_message_update else event
    if not isinstance(updated_message, dict):
        return {"status": "ignored", "reason": "Invalid updated message"}

    channel_id = event.get("channel")
    event_ts = event.get("event_ts") or event.get("ts")
    original_message_ts = updated_message.get("ts")
    thread_ts = updated_message.get("thread_ts") or original_message_ts
    user_id = updated_message.get("user")
    text = updated_message.get("text")
    attachments = updated_message.get("attachments", [])
    if not (
        isinstance(channel_id, str)
        and channel_id
        and isinstance(event_ts, str)
        and event_ts
        and isinstance(original_message_ts, str)
        and original_message_ts
        and isinstance(thread_ts, str)
        and thread_ts
        and isinstance(user_id, str)
        and user_id
        and isinstance(text, str)
    ):
        return {"status": "ignored", "reason": "Missing channel/message fields"}
    if not isinstance(attachments, list):
        attachments = []
    if is_message_update:
        previous_message = event.get("previous_message")
        if not isinstance(previous_message, dict):
            return {"status": "ignored", "reason": "Invalid previous message"}
        previous_ts = previous_message.get("ts")
        previous_thread_ts = previous_message.get("thread_ts") or previous_ts
        if (
            previous_message.get("user") != user_id
            or previous_ts != original_message_ts
            or previous_thread_ts != thread_ts
        ):
            return {"status": "ignored", "reason": "Updated message identity changed"}

    is_direct_message = (
        not is_message_update and event.get("channel_type") == "im" and bool(user_id)
    )
    is_untagged_two_party_reply = False
    if event.get("type") != "app_mention" and not is_message_update:
        has_username_mention = bool(
            common.SLACK_BOT_USERNAME and f"@{common.SLACK_BOT_USERNAME}" in text
        )
        has_id_mention = bool(bot_user_id and f"<@{bot_user_id}>" in text)
        is_ready_plan_reply = bool(
            not is_direct_message
            and await service._slack_user_can_reply_to_ready_plan(
                channel_id,
                str(event.get("thread_ts") or ""),
                user_id,
            )
        )
        is_untagged_two_party_reply = bool(
            not event.get("subtype")
            and not is_direct_message
            and not has_username_mention
            and not has_id_mention
            and await service._slack_thread_allows_untagged_reply(
                channel_id,
                str(event.get("thread_ts") or ""),
                text,
                bot_user_id,
                user_id,
                event_ts,
            )
        )
        should_handle_message = any(
            (
                has_username_mention,
                has_id_mention,
                is_ready_plan_reply,
                is_direct_message,
                is_untagged_two_party_reply,
            )
        )
        if not should_handle_message:
            return {"status": "ignored", "reason": "Not an app mention, DM, or plan reply"}

    if (
        event.get("subtype") == "bot_message"
        or event.get("bot_id")
        or updated_message.get("subtype") == "bot_message"
        or updated_message.get("bot_id")
    ):
        return {"status": "ignored", "reason": "Event from a bot"}

    if bot_user_id and user_id == bot_user_id:
        return {"status": "ignored", "reason": "Event from this bot user"}

    if is_message_update:
        if await common.claim_slack_event(event_id, channel_id, event_ts):
            event_data = {
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "event_ts": event_ts,
                "original_message_ts": original_message_ts,
                "user_id": user_id,
                "text": text,
                "attachments": attachments,
                "bot_user_id": bot_user_id,
                "message_update": True,
            }
            background_tasks.add_task(
                _process_slack_message_update,
                event_data,
                channel_id,
                thread_ts,
                original_message_ts,
                user_id,
            )
            return {"status": "accepted", "message": "Slack update queued"}
        return {"status": "ignored", "reason": "Duplicate Slack event delivery"}

    langgraph_client = common.get_client(url=common.LANGGRAPH_URL)
    thread_id: str | None = None
    channel_context = await common._get_slack_channel_context(channel_id)

    if await common._is_docs_plz_slack_channel(channel_id, channel_context):
        if await common.claim_slack_event(event_id, channel_id, event_ts):
            background_tasks.add_task(
                common.post_slack_thread_reply,
                channel_id,
                thread_ts,
                common.DOCS_PLZ_SLACK_GATE_REPLY,
            )
            return {"status": "accepted", "message": "Slack mention gated for docs-plz"}
    else:
        if not is_message_update:
            try:
                thread_id = await common.resolve_slack_thread_id(
                    langgraph_client, channel_id, thread_ts
                )
            except common.SlackThreadMappingError:
                common.logger.exception("Could not resolve explicit Slack thread mapping")
                await common.post_slack_thread_reply(
                    channel_id,
                    thread_ts,
                    "Open SWE found conflicting state for this Slack thread and will not guess which agent thread to use.",
                )
                return {"status": "error", "message": "Conflicting Slack thread mapping"}
        event_data = {
            "channel_id": channel_id,
            "channel_context": channel_context,
            "thread_ts": thread_ts,
            "event_ts": event_ts,
            "original_message_ts": original_message_ts,
            "user_id": user_id,
            "text": text,
            "attachments": attachments,
            "bot_user_id": bot_user_id,
            "thread_id": thread_id,
            "treat_all_messages_as_mentions": is_direct_message,
            "untagged_reply": is_untagged_two_party_reply,
            "message_update": is_message_update,
        }
        repo_config = await common.get_slack_repo_config(
            channel_id,
            thread_ts,
            slack_user_id=user_id,
            channel_context=channel_context,
            thread_id=thread_id,
        )
        if await common.claim_slack_event(event_id, channel_id, event_ts):
            background_tasks.add_task(service.process_slack_mention, event_data, repo_config)
            return {"status": "accepted", "message": "Slack mention queued"}

    common.logger.info("Ignoring duplicate delivery of Slack event %s", event_id)
    return {"status": "ignored", "reason": "Duplicate Slack event delivery"}


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

        thread_id = await common.lookup_slack_thread_id(
            common.get_client(url=common.LANGGRAPH_URL), channel_id, thread_ts
        )
        if not thread_id:
            return {"status": "ignored", "reason": "Slack thread is not associated"}
        if not await common._slack_user_is_thread_owner(thread_id, user_id):
            await common.post_slack_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text="Only the person who requested this run can approve workflow file pushes.",
                agent_thread_id=thread_id,
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
                agent_thread_id=thread_id,
            )
            return {"status": "ignored", "reason": "workflow approval not found"}
        background_tasks.add_task(
            _update_selected_option_message,
            payload,
            action,
            "Approve workflow push" if approved else "Reject workflow push",
        )
        if not approved:
            await common.post_slack_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=f"Workflow push rejected for fingerprint `{fingerprint}`. No workflow files will be pushed.",
                agent_thread_id=thread_id,
            )
            return {"status": "accepted", "message": "Workflow push rejected"}

        await common.post_slack_thread_reply(
            channel_id=channel_id,
            thread_ts=thread_ts,
            text=f"Workflow push approved for fingerprint `{fingerprint}`. Open SWE will retry the blocked push.",
            agent_thread_id=thread_id,
        )
        channel_context = await common._get_slack_channel_context(channel_id)
        repo_config = await common.get_slack_repo_config(
            channel_id,
            thread_ts,
            slack_user_id=user_id,
            channel_context=channel_context,
            thread_id=thread_id,
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
                "thread_id": thread_id,
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

        thread_id = await common.lookup_slack_thread_id(
            common.get_client(url=common.LANGGRAPH_URL), channel_id, thread_ts
        )
        if not thread_id:
            return {"status": "ignored", "reason": "Slack thread is not associated"}

        if plan_action == "cancel":
            background_tasks.add_task(
                _update_selected_option_message, payload, action, "Cancel plan"
            )
            await common.post_slack_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text="Plan cancelled. No changes will be made.",
                agent_thread_id=thread_id,
            )
            return {"status": "accepted", "message": "Plan cancelled"}

        if plan_action == "approve":
            user_name = str(user.get("name") or user.get("username") or user_id)
            background_tasks.add_task(
                _update_selected_option_message, payload, action, "Approve plan"
            )
            channel_context = await common._get_slack_channel_context(channel_id)
            repo_config = await common.get_slack_repo_config(
                channel_id, thread_ts, slack_user_id=user_id, channel_context=channel_context
            )
            background_tasks.add_task(
                service.process_slack_plan_approval,
                {
                    "thread_id": thread_id,
                    "channel_id": channel_id,
                    "channel_context": channel_context,
                    "thread_ts": thread_ts,
                    "event_ts": str(message.get("ts") or ""),
                    "user_id": user_id,
                    "user_name": user_name,
                    "text": "approve",
                    "bot_user_id": common.SLACK_BOT_USER_ID,
                },
                repo_config,
            )
            return {"status": "accepted", "message": "Plan approval queued"}

        background_tasks.add_task(
            _update_selected_option_message, payload, action, "Request plan changes"
        )
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

    thread_id = await common.lookup_slack_thread_id(
        common.get_client(url=common.LANGGRAPH_URL), channel_id, thread_ts
    )
    if not thread_id:
        return {"status": "ignored", "reason": "Slack thread is not associated"}
    channel_context = await common._get_slack_channel_context(channel_id)
    repo_config = await common.get_slack_repo_config(
        channel_id,
        thread_ts,
        slack_user_id=user_id,
        channel_context=channel_context,
        thread_id=thread_id,
    )
    background_tasks.add_task(_update_selected_option_message, payload, action, response)
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
            "thread_id": thread_id,
        },
        repo_config,
    )
    return {"status": "accepted", "message": "Slack option queued"}


async def _update_selected_option_message(
    payload: dict[str, common.Any],
    action: dict[str, common.Any],
    fallback_label: str,
) -> None:
    channel_value = payload.get("channel")
    channel = channel_value if isinstance(channel_value, dict) else {}
    message_value = payload.get("message")
    message = message_value if isinstance(message_value, dict) else {}
    container_value = payload.get("container")
    container = container_value if isinstance(container_value, dict) else {}
    channel_id = str(channel.get("id") or container.get("channel_id") or "")
    message_ts = str(message.get("ts") or container.get("message_ts") or "")
    action_text_value = action.get("text")
    action_text = action_text_value if isinstance(action_text_value, dict) else {}
    label = str(action_text.get("text") or fallback_label).strip()[:150]
    blocks = _selected_option_blocks(message, label)
    if not channel_id or not message_ts or not label or not blocks:
        return

    try:
        ok, error = await common.update_slack_message(
            channel_id,
            message_ts,
            str(message.get("text") or label),
            blocks=blocks,
        )
    except Exception:
        common.logger.warning(
            "Could not persist Slack option selection: channel=%s ts=%s",
            channel_id,
            message_ts,
            exc_info=True,
        )
        return
    if not ok:
        common.logger.warning(
            "Could not persist Slack option selection: channel=%s ts=%s error=%s",
            channel_id,
            message_ts,
            error,
        )


def _selected_option_blocks(
    message: dict[str, common.Any], label: str
) -> list[dict[str, common.Any]]:
    raw_blocks = message.get("blocks")
    if not isinstance(raw_blocks, list):
        return []

    selected_block: dict[str, common.Any] = {
        "type": "context",
        "elements": [{"type": "plain_text", "text": f"Selected: {label}"}],
    }
    updated_blocks: list[dict[str, common.Any]] = []
    replaced = False
    for block in raw_blocks:
        if not isinstance(block, dict):
            continue
        elements = block.get("elements")
        if block.get("type") != "actions" or _first_open_swe_option_action(elements) is None:
            updated_blocks.append(block)
            continue
        if not replaced:
            updated_blocks.append(selected_block)
            replaced = True
        if isinstance(elements, list):
            remaining = [
                element for element in elements if _first_open_swe_option_action([element]) is None
            ]
            if remaining:
                updated_blocks.append({**block, "elements": remaining})

    return updated_blocks if replaced else []


def _first_open_swe_option_action(actions: common.Any) -> dict[str, common.Any] | None:
    if not isinstance(actions, list):
        return None
    for action in actions:
        action_id = action.get("action_id") if isinstance(action, dict) else None
        if isinstance(action_id, str) and (
            action_id == "open_swe_option_select" or action_id.startswith("open_swe_option_select_")
        ):
            return action
    return None


@router.get("/webhooks/slack")
async def slack_webhook_verify() -> dict[str, str]:
    """Verify endpoint for Slack webhook setup."""
    return {"status": "ok", "message": "Slack webhook endpoint is active"}
