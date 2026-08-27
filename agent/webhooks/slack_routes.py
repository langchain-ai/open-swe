"""Slack webhook HTTP routes."""

import asyncio
import hashlib
from time import time_ns
from typing import Any

from fastapi import APIRouter
from langgraph_sdk.client import LangGraphClient

from ..utils.thread_ops import langgraph_client as get_langgraph_client
from . import common
from . import slack as service

router = APIRouter()

_MESSAGE_UPDATE_RETRY_DELAYS = (0.1, 0.2, 0.5, 1, 2, 4, 8, 14)
_EXTERNAL_CHANNEL_REFUSAL = "Open SWE does not operate in channels with external participants."


def _event_channel_id(event: dict[str, Any]) -> str:
    channel = event.get("channel")
    if isinstance(channel, str):
        return channel
    if isinstance(channel, dict) and isinstance(channel.get("id"), str):
        return channel["id"]
    item = event.get("item")
    if isinstance(item, dict) and isinstance(item.get("channel"), str):
        return item["channel"]
    channel_id = event.get("channel_id")
    return channel_id if isinstance(channel_id, str) else ""


def _synthetic_slack_ts() -> str:
    timestamp = time_ns()
    return f"{timestamp // 1_000_000_000}.{timestamp % 1_000_000_000:09d}"


def _bounded_payload_text(label: str, payload: dict[str, Any]) -> str:
    serialized = common.json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{label}\n```json\n{serialized[:8000]}\n```"


async def _queue_code_channel_turn(
    background_tasks: common.BackgroundTasks,
    *,
    channel_id: str,
    user_id: str,
    text: str,
    event_id: str,
    event_ts: str,
    explicit_request: bool,
) -> dict[str, str]:
    if not channel_id or not user_id or not text or not event_id or not event_ts:
        return {"status": "ignored", "reason": "Missing code channel interaction fields"}
    if not await common.is_code_channel(channel_id):
        return {"status": "ignored", "reason": "Not a code channel"}

    client = get_langgraph_client()
    thread_id = await common.lookup_slack_thread_id(
        client, channel_id, common.CODE_CHANNEL_SESSION_TS
    )
    if not thread_id:
        return {"status": "ignored", "reason": "Code channel is not associated"}
    if not await common.claim_slack_event(event_id, channel_id, event_ts):
        return {"status": "ignored", "reason": "Duplicate code channel interaction"}

    channel_context = await common._get_slack_channel_context(channel_id)
    repo_config = await common.get_slack_repo_config(
        channel_id,
        common.CODE_CHANNEL_SESSION_TS,
        slack_user_id=user_id,
        channel_context=channel_context,
        thread_id=thread_id,
    )
    background_tasks.add_task(
        service.process_slack_mention,
        {
            "channel_id": channel_id,
            "channel_context": channel_context,
            "thread_ts": common.CODE_CHANNEL_SESSION_TS,
            "event_ts": event_ts,
            "original_message_ts": event_ts,
            "user_id": user_id,
            "text": text,
            "attachments": [],
            "bot_user_id": common.SLACK_BOT_USER_ID,
            "thread_id": thread_id,
            "treat_all_messages_as_mentions": True,
            "code_channel": True,
            "explicit_request": explicit_request,
        },
        repo_config,
    )
    return {"status": "accepted", "message": "Code channel interaction queued"}


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
    langgraph_client = get_langgraph_client()
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
    channel_context = await common._get_slack_channel_context(channel_id, use_cache=False)
    if not common.slack_channel_allows_operations(channel_context):
        common.logger.warning("Blocked Slack message update in ineligible channel=%s", channel_id)
        return
    event_data["thread_id"] = thread_id
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
    if not isinstance(event, dict):
        return {"status": "ignored", "reason": "Invalid Slack event"}

    event_id = str(payload.get("event_id") or "")
    channel_id = _event_channel_id(event)
    channel_context: dict[str, Any] | None = None
    if channel_id:
        channel_context = await common._get_slack_channel_context(channel_id, use_cache=False)
        if not common.slack_channel_allows_operations(channel_context):
            is_external = channel_context.get("is_ext_shared") is True
            event_ts = str(event.get("event_ts") or event.get("ts") or "")
            thread_ts = str(event.get("thread_ts") or event.get("ts") or "")
            if (
                is_external
                and event.get("type") == "app_mention"
                and event.get("subtype") is None
                and isinstance(event.get("user"), str)
                and thread_ts
                and await common.claim_slack_event(event_id, channel_id, event_ts)
            ):
                background_tasks.add_task(
                    common.post_slack_thread_reply,
                    channel_id,
                    thread_ts,
                    _EXTERNAL_CHANNEL_REFUSAL,
                )
            common.logger.warning(
                "Blocked Slack event in %s channel=%s",
                "external" if is_external else "unverified",
                channel_id,
            )
            return {"status": "ignored", "reason": "Slack channel is not eligible"}

    if event.get("type") == "code_channel_action":
        action_value = event.get("action")
        item_value = event.get("item")
        action: dict[str, Any] = action_value if isinstance(action_value, dict) else {}
        item: dict[str, Any] = item_value if isinstance(item_value, dict) else {}
        user = event.get("user")
        user_id = str(
            user.get("id") if isinstance(user, dict) else user or event.get("user_id") or ""
        )
        channel = event.get("channel")
        action_channel_id = str(
            channel.get("id")
            if isinstance(channel, dict)
            else channel or event.get("channel_id") or ""
        )
        event_ts = str(event.get("event_ts") or event.get("action_ts") or _synthetic_slack_ts())
        action_payload = {
            "key": event.get("key") or action.get("key") or item.get("key"),
            "label": event.get("label") or action.get("label") or item.get("label"),
            "value": event.get("value") or action.get("value") or item.get("value"),
        }
        return await _queue_code_channel_turn(
            background_tasks,
            channel_id=action_channel_id,
            user_id=user_id,
            text=_bounded_payload_text(
                "A code channel context-bar action was selected.", action_payload
            ),
            event_id=event_id or f"code-channel-action:{action_channel_id}:{event_ts}",
            event_ts=event_ts,
            explicit_request=True,
        )

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

    if event.get("type") == "agent_session_stopped":
        background_tasks.add_task(
            common.process_agent_session_stopped, event, str(payload.get("event_id") or "")
        )
        return {"status": "accepted", "message": "Session stop queued"}

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

    event_ts = event.get("event_ts") or event.get("ts")
    original_message_ts = updated_message.get("ts")
    thread_ts = updated_message.get("thread_ts") or original_message_ts
    reply_thread_ts = updated_message.get("thread_ts")
    if not isinstance(reply_thread_ts, str):
        reply_thread_ts = ""
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

    # A code channel is one session for the whole channel, so every message in it
    # routes to the same agent thread and is treated as directed at the agent.
    in_code_channel = await common.is_code_channel(channel_id)
    if in_code_channel:
        thread_ts = common.CODE_CHANNEL_SESSION_TS

    is_direct_message = (
        not is_message_update and event.get("channel_type") == "im" and bool(user_id)
    )
    is_untagged_two_party_reply = False
    if event.get("type") != "app_mention" and not is_message_update and not in_code_channel:
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
                "code_channel": in_code_channel,
                "reply_thread_ts": reply_thread_ts if in_code_channel else "",
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

    langgraph_client = get_langgraph_client()
    thread_id: str | None = None
    if channel_context is None:
        return {"status": "ignored", "reason": "Slack channel is not eligible"}

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
            "treat_all_messages_as_mentions": is_direct_message or in_code_channel,
            "untagged_reply": is_untagged_two_party_reply,
            "message_update": is_message_update,
            "code_channel": in_code_channel,
            "reply_thread_ts": reply_thread_ts if in_code_channel else "",
            "app_context": updated_message.get("app_context") or event.get("app_context"),
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


@router.post("/webhooks/slack/code-channel-commands")
async def slack_code_channel_command(
    request: common.Request, background_tasks: common.BackgroundTasks
) -> dict[str, str]:
    """Handle runtime slash commands registered for a Slack code channel."""
    body = await request.body()
    signature = request.headers.get("X-Slack-Signature", "")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    if not common.verify_slack_signature(
        body=body,
        timestamp=timestamp,
        signature=signature,
        secret=common.SLACK_SIGNING_SECRET,
    ):
        common.logger.warning("Invalid Slack code channel command signature")
        raise common.HTTPException(status_code=401, detail="Invalid signature")

    form = common.parse_qs(body.decode("utf-8"))
    value = lambda key: str((form.get(key) or [""])[0]).strip()  # noqa: E731
    channel_id = value("channel_id")
    user_id = value("user_id")
    command = value("command").removeprefix("/")
    command_text = value("text")
    trigger_id = value("trigger_id")
    if not (channel_id and user_id and 1 <= len(command) <= 31 and len(command_text) <= 4000):
        return {"response_type": "ephemeral", "text": "That code-channel command was invalid."}

    event_ts = _synthetic_slack_ts()
    event_id = f"code-channel-command:{trigger_id or hashlib.sha256(body).hexdigest()}"
    command_line = f"/{command}{f' {command_text}' if command_text else ''}"
    result = await _queue_code_channel_turn(
        background_tasks,
        channel_id=channel_id,
        user_id=user_id,
        text=f"A runtime code-channel command was invoked: {command_line}",
        event_id=event_id,
        event_ts=event_ts,
        explicit_request=True,
    )
    if result["status"] != "accepted":
        return {
            "response_type": "ephemeral",
            "text": "Open SWE could not route that command to this code channel.",
        }
    return {"response_type": "ephemeral", "text": f"Working on /{command}…"}


@router.post("/webhooks/slack/interactivity")
async def slack_interactivity(
    request: common.Request, background_tasks: common.BackgroundTasks
) -> dict[str, Any]:
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

    container = payload.get("container") if isinstance(payload.get("container"), dict) else {}
    if payload.get("type") == "block_suggestion" and container.get("type") == "code_channel_view":
        channel_id = str(container.get("channel_id") or "")
        view_id = str(container.get("view_id") or "")
        action_id = str(payload.get("action_id") or "")
        if not channel_id or not view_id or not action_id:
            return {"options": []}
        options = await common.get_block_suggestions(
            get_langgraph_client(),
            channel_id,
            view_id,
            action_id,
            str(payload.get("value") or "")[:200],
        )
        return {"options": options}
    if payload.get("type") == "block_actions" and container.get("type") == "code_channel_view":
        actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        first_action = actions[0] if actions and isinstance(actions[0], dict) else {}
        event_ts = str(first_action.get("action_ts") or _synthetic_slack_ts())
        safe_actions = [
            {
                key: action[key]
                for key in (
                    "action_id",
                    "type",
                    "value",
                    "selected_option",
                    "selected_options",
                    "selected_user",
                    "selected_users",
                    "selected_conversation",
                    "selected_conversations",
                    "selected_channel",
                    "selected_channels",
                    "selected_date",
                    "selected_time",
                    "selected_date_time",
                )
                if key in action
            }
            for action in actions[:10]
            if isinstance(action, dict)
        ]
        interaction = {
            "view_id": container.get("view_id"),
            "actions": safe_actions,
        }
        payload_fingerprint = hashlib.sha256(payload_raw.encode()).hexdigest()
        return await _queue_code_channel_turn(
            background_tasks,
            channel_id=str(container.get("channel_id") or ""),
            user_id=str(user.get("id") or ""),
            text=_bounded_payload_text(
                "A user interacted with a code channel Block Kit view.", interaction
            ),
            event_id=f"code-channel-view:{payload.get('trigger_id') or payload_fingerprint}",
            event_ts=event_ts,
            explicit_request=True,
        )

    action = _first_open_swe_option_action(payload.get("actions"))
    if action is None:
        return {"status": "ignored", "reason": "No Open SWE action"}

    channel = payload.get("channel") if isinstance(payload.get("channel"), dict) else {}
    container = payload.get("container") if isinstance(payload.get("container"), dict) else {}
    channel_id = str(channel.get("id") or container.get("channel_id") or "")
    if not channel_id:
        return {"status": "ignored", "reason": "Slack channel is not eligible"}
    channel_context = await common._get_slack_channel_context(channel_id, use_cache=False)
    if not common.slack_channel_allows_operations(channel_context):
        common.logger.warning("Blocked Slack interaction in ineligible channel=%s", channel_id)
        return {"status": "ignored", "reason": "Slack channel is not eligible"}

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
            get_langgraph_client(), channel_id, thread_ts
        )
        if not thread_id:
            return {"status": "ignored", "reason": "Slack thread is not associated"}
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
            get_langgraph_client(), channel_id, thread_ts
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

    thread_id = await common.lookup_slack_thread_id(get_langgraph_client(), channel_id, thread_ts)
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
