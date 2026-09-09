"""Slack webhook HTTP routes."""

import asyncio
import hashlib
from time import time_ns

from fastapi import APIRouter
from langgraph_sdk.client import LangGraphClient

from agent.slack import webhook as service
from agent.slack.client import SlackChannelContext
from agent.slack.failures import (
    SlackRequestError,
    SlackRequestTarget,
    answer_slack_request,
    run_slack_task,
)
from agent.slack.payloads import (
    SlackBlockAction,
    SlackButtonValue,
    SlackEventEnvelope,
    SlackInteraction,
    SlackInteractionMessage,
    parse_json_object,
)
from agent.slack.request import SlackRequest
from agent.slack.responses import (
    BlockSuggestionResponse,
    ChallengeResponse,
    FeedbackResponse,
    HealthResponse,
    SlashCommandResponse,
    WebhookResponse,
    accepted,
    ephemeral,
    ignored,
)
from agent.slack.thread_feedback import handle_slack_feedback_interaction, is_slack_feedback_payload
from agent.utils.json_types import JsonObject
from agent.utils.thread_ops import langgraph_client as get_langgraph_client
from agent.webhooks import common

router = APIRouter()

_MESSAGE_UPDATE_RETRY_DELAYS = (0.1, 0.2, 0.5, 1, 2, 4, 8, 14)
_EXTERNAL_CHANNEL_REFUSAL = "Open SWE does not operate in channels with external participants."
_OPTION_ACTION_ID = "open_swe_option_select"


def _synthetic_slack_ts() -> str:
    timestamp = time_ns()
    return f"{timestamp // 1_000_000_000}.{timestamp % 1_000_000_000:09d}"


def _bounded_payload_text(label: str, payload: JsonObject) -> str:
    serialized = common.json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{label}\n```json\n{serialized[:8000]}\n```"


def _verify_signature(request: common.Request, body: bytes, what: str) -> None:
    if not common.verify_slack_signature(
        body=body,
        timestamp=request.headers.get("X-Slack-Request-Timestamp", ""),
        signature=request.headers.get("X-Slack-Signature", ""),
        secret=common.SLACK_SIGNING_SECRET,
    ):
        common.logger.warning("Invalid Slack signature", extra={"slack_endpoint": what})
        raise common.HTTPException(status_code=401, detail="Invalid signature")


async def _queue_code_channel_turn(
    background_tasks: common.BackgroundTasks,
    *,
    channel_id: str,
    user_id: str,
    text: str,
    event_id: str,
    event_ts: str,
    explicit_request: bool,
    team_id: str = "",
) -> WebhookResponse:
    if not channel_id or not user_id or not text or not event_id or not event_ts:
        return ignored("Missing code channel interaction fields")
    if not await common.is_code_channel(channel_id):
        return ignored("Not a code channel")

    target = SlackRequestTarget(
        channel_id=channel_id, thread_ts=common.CODE_CHANNEL_SESSION_TS, event_id=event_id
    )

    async def dispatch() -> WebhookResponse:
        client = get_langgraph_client()
        thread_id = await common.lookup_slack_thread_id(
            client, channel_id, common.CODE_CHANNEL_SESSION_TS
        )
        if not thread_id:
            return ignored("Code channel is not associated")
        if not await common.claim_slack_event(event_id, channel_id, event_ts):
            return ignored("Duplicate code channel interaction")

        channel_context = await common.resolve_slack_channel_context(channel_id)
        repo = await common.get_slack_repo_config(
            channel_id,
            common.CODE_CHANNEL_SESSION_TS,
            slack_user_id=user_id,
            channel_context=channel_context,
            thread_id=thread_id,
        )
        background_tasks.add_task(
            service.process_slack_mention,
            SlackRequest(
                channel_id=channel_id,
                channel_context=channel_context,
                thread_ts=common.CODE_CHANNEL_SESSION_TS,
                event_ts=event_ts,
                original_message_ts=event_ts,
                event_id=event_id,
                user_id=user_id,
                text=text,
                bot_user_id=common.SLACK_BOT_USER_ID,
                thread_id=thread_id,
                treat_all_messages_as_mentions=True,
                code_channel=True,
                explicit_request=explicit_request,
                team_id=team_id,
            ),
            repo,
        )
        return accepted("Code channel interaction queued")

    return await answer_slack_request(target, dispatch)


async def _lookup_delivered_message_update(
    langgraph_client: LangGraphClient,
    channel_id: str,
    thread_ts: str,
    message_ts: str,
    user_id: str,
) -> tuple[str | None, JsonObject | None]:
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
            if await common.thread_exists(thread_id):
                return thread_id, delivered_message
        if delay is None:
            break
        await asyncio.sleep(delay)
    return None, None


async def _process_slack_message_update(request: SlackRequest) -> None:
    await run_slack_task(request.target, _process_slack_message_update_impl(request))


async def _process_slack_message_update_impl(request: SlackRequest) -> None:
    langgraph_client = get_langgraph_client()
    thread_id, delivered_message = await _lookup_delivered_message_update(
        langgraph_client,
        request.channel_id,
        request.thread_ts,
        request.original_message_ts,
        request.user_id,
    )
    if not thread_id or not delivered_message:
        common.logger.info(
            "Ignoring undelivered Slack message update channel=%s message=%s",
            request.channel_id,
            request.original_message_ts,
        )
        return
    channel_context = await common.resolve_slack_channel_context(
        request.channel_id, use_cache=False
    )
    if not common.slack_channel_allows_operations(channel_context):
        common.logger.warning(
            "Blocked Slack message update in ineligible channel=%s", request.channel_id
        )
        return
    repo = await common.get_slack_repo_config(
        request.channel_id,
        request.thread_ts,
        slack_user_id=request.user_id,
        channel_context=channel_context,
        thread_id=thread_id,
    )
    await service.process_slack_mention(
        request.model_copy(update={"thread_id": thread_id, "channel_context": channel_context}),
        repo,
    )


@router.post("/webhooks/slack")
async def slack_webhook(
    request: common.Request, background_tasks: common.BackgroundTasks
) -> WebhookResponse | ChallengeResponse:
    """Handle Slack Event API webhooks for app mentions."""
    body = await request.body()
    _verify_signature(request, body, "events")

    payload = parse_json_object(body)
    if payload is None:
        common.logger.warning("Failed to parse Slack webhook JSON")
        return {"status": "error", "message": "Invalid JSON"}
    envelope = SlackEventEnvelope.parse(payload)
    if envelope is None:
        return ignored("Invalid Slack event")

    if envelope.type == "url_verification":
        return {"challenge": envelope.challenge}
    if envelope.type != "event_callback":
        return ignored("Not an event callback")
    event = envelope.event
    if event is None:
        return ignored("Invalid Slack event")
    raw_event = payload.get("event")
    if not isinstance(raw_event, dict):
        return ignored("Invalid Slack event")

    event_id = envelope.event_id
    team_id = envelope.team_id or event.team
    channel_id = event.resolve_channel_id()
    channel_context: SlackChannelContext | None = None
    if channel_id:
        channel_context = await common.resolve_slack_channel_context(channel_id, use_cache=False)
        if not common.slack_channel_allows_operations(channel_context):
            is_external = channel_context.get("is_ext_shared") is True
            event_ts = event.event_ts or event.ts
            thread_ts = event.thread_ts or event.ts
            if (
                is_external
                and event.type == "app_mention"
                and not event.subtype
                and isinstance(event.user, str)
                and event.user
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
            return ignored("Slack channel is not eligible")

    if event.type == "code_channel_action":
        action = event.action
        item = event.item
        event_ts = event.event_ts or event.action_ts or _synthetic_slack_ts()
        action_payload: JsonObject = {
            "key": event.key or (action and action.key) or (item and item.key),
            "label": event.label or (action and action.label) or (item and item.label),
            "value": event.value or (action and action.value) or (item and item.value),
        }
        return await _queue_code_channel_turn(
            background_tasks,
            channel_id=channel_id,
            user_id=event.resolve_user_id(),
            text=_bounded_payload_text(
                "A code channel context-bar action was selected.", action_payload
            ),
            event_id=event_id or f"code-channel-action:{channel_id}:{event_ts}",
            event_ts=event_ts,
            explicit_request=True,
            team_id=team_id,
        )

    if event.type == "reaction_added":
        if event.reaction == "x":
            background_tasks.add_task(common.process_slack_stop_reaction, raw_event, event_id)
            return accepted("Stop reaction queued")
        if event.reaction in common.FEEDBACK_REACTIONS:
            background_tasks.add_task(common.process_slack_reaction_added, raw_event, event_id)
            return accepted("Reaction feedback queued")
        return ignored("Reaction not tracked for feedback")

    if event.type == "reaction_removed":
        if event.reaction in common.FEEDBACK_REACTIONS:
            background_tasks.add_task(common.process_slack_reaction_removed, raw_event, event_id)
            return accepted("Reaction removal queued")
        return ignored("Reaction not tracked for feedback")

    if event.type == "agent_session_stopped":
        background_tasks.add_task(common.process_agent_session_stopped, raw_event, event_id)
        return accepted("Session stop queued")

    retry_num = request.headers.get("X-Slack-Retry-Num", "")
    if retry_num and await common.slack_event_already_seen(event_id):
        common.logger.info(
            "Ignoring Slack retry %s of already-handled event %s", retry_num, event_id
        )
        return ignored("Duplicate Slack event delivery")

    bot_user_id = envelope.bot_user_id(common.SLACK_BOT_USER_ID)

    is_message_update = event.type == "message" and event.subtype == "message_changed"
    updated_message = event.message if is_message_update else event
    if updated_message is None:
        return ignored("Invalid updated message")
    event_ts = event.event_ts or event.ts
    original_message_ts = updated_message.ts
    reply_thread_ts = updated_message.thread_ts
    thread_ts = reply_thread_ts or original_message_ts
    user_id = updated_message.user if isinstance(updated_message.user, str) else ""
    text = updated_message.text
    attachments = updated_message.attachments
    if not (channel_id and event_ts and original_message_ts and thread_ts and user_id) or (
        text is None
    ):
        return ignored("Missing channel/message fields")
    if is_message_update:
        previous_message = event.previous_message
        if previous_message is None:
            return ignored("Invalid previous message")
        previous_thread_ts = previous_message.thread_ts or previous_message.ts
        if (
            previous_message.user != user_id
            or previous_message.ts != original_message_ts
            or previous_thread_ts != thread_ts
        ):
            return ignored("Updated message identity changed")
        # Link unfurls arrive as edits that only add `attachments`, so comparing
        # them starts a run for text the agent has already been given.
        if text == previous_message.text:
            return ignored("No user-visible message changes")

    # A code channel is one session for the whole channel, so every message in it
    # routes to the same agent thread and is treated as directed at the agent.
    in_code_channel = await common.is_code_channel(channel_id)
    if in_code_channel:
        thread_ts = common.CODE_CHANNEL_SESSION_TS

    is_direct_message = not is_message_update and event.channel_type == "im" and bool(user_id)
    is_untagged_two_party_reply = False
    if event.type != "app_mention" and not is_message_update and not in_code_channel:
        has_username_mention = bool(
            common.SLACK_BOT_USERNAME and f"@{common.SLACK_BOT_USERNAME}" in text
        )
        has_id_mention = bool(bot_user_id and f"<@{bot_user_id}>" in text)
        is_ready_plan_reply = bool(
            not is_direct_message
            and await service.slack_user_can_reply_to_ready_plan(
                channel_id, event.thread_ts, user_id
            )
        )
        is_untagged_two_party_reply = bool(
            not event.subtype
            and not is_direct_message
            and not has_username_mention
            and not has_id_mention
            and await service.slack_thread_allows_untagged_reply(
                channel_id,
                event.thread_ts,
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
            return ignored("Not an app mention, DM, or plan reply")

    if event.is_from_bot or updated_message.is_from_bot:
        return ignored("Event from a bot")

    if bot_user_id and user_id == bot_user_id:
        return ignored("Event from this bot user")

    # From here on the message is addressed to Open SWE: any failure is reported to the thread.
    target = SlackRequestTarget(channel_id=channel_id, thread_ts=thread_ts, event_id=event_id)

    async def dispatch() -> WebhookResponse:
        if is_message_update:
            if await common.claim_slack_event(event_id, channel_id, event_ts):
                background_tasks.add_task(
                    _process_slack_message_update,
                    SlackRequest(
                        channel_id=channel_id,
                        thread_ts=thread_ts,
                        event_ts=event_ts,
                        original_message_ts=original_message_ts,
                        event_id=event_id,
                        user_id=user_id,
                        text=text,
                        attachments=attachments,
                        bot_user_id=bot_user_id,
                        message_update=True,
                        code_channel=in_code_channel,
                        reply_thread_ts=reply_thread_ts if in_code_channel else "",
                    ),
                )
                return accepted("Slack update queued")
            return ignored("Duplicate Slack event delivery")

        if channel_context is None:
            return ignored("Slack channel is not eligible")

        try:
            thread_id = await common.resolve_slack_thread_id(
                get_langgraph_client(), channel_id, thread_ts
            )
        except common.SlackThreadMappingError as exc:
            raise SlackRequestError(
                "Open SWE found conflicting state for this Slack thread and will not guess "
                "which agent thread to use."
            ) from exc
        repo = await common.get_slack_repo_config(
            channel_id,
            thread_ts,
            slack_user_id=user_id,
            channel_context=channel_context,
            thread_id=thread_id,
        )
        if await common.claim_slack_event(event_id, channel_id, event_ts):
            background_tasks.add_task(
                service.process_slack_mention,
                SlackRequest(
                    channel_id=channel_id,
                    channel_context=channel_context,
                    thread_ts=thread_ts,
                    event_ts=event_ts,
                    original_message_ts=original_message_ts,
                    event_id=event_id,
                    user_id=user_id,
                    text=text,
                    attachments=attachments,
                    bot_user_id=bot_user_id,
                    thread_id=thread_id,
                    treat_all_messages_as_mentions=is_direct_message or in_code_channel,
                    untagged_reply=is_untagged_two_party_reply,
                    code_channel=in_code_channel,
                    reply_thread_ts=reply_thread_ts if in_code_channel else "",
                    team_id=team_id,
                ),
                repo,
            )
            return accepted("Slack mention queued")

        common.logger.info("Ignoring duplicate delivery of Slack event %s", event_id)
        return ignored("Duplicate Slack event delivery")

    return await answer_slack_request(target, dispatch)


@router.post("/webhooks/slack/code-channel-commands")
async def slack_code_channel_command(
    request: common.Request, background_tasks: common.BackgroundTasks
) -> SlashCommandResponse:
    """Handle runtime slash commands registered for a Slack code channel."""
    body = await request.body()
    _verify_signature(request, body, "code-channel-commands")

    form = common.parse_qs(body.decode("utf-8"))
    value = lambda key: str((form.get(key) or [""])[0]).strip()  # noqa: E731
    channel_id = value("channel_id")
    user_id = value("user_id")
    command = value("command").removeprefix("/")
    command_text = value("text")
    trigger_id = value("trigger_id")
    team_id = value("team_id")
    if not (channel_id and user_id and 1 <= len(command) <= 31 and len(command_text) <= 4000):
        return ephemeral("That code-channel command was invalid.")

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
        team_id=team_id,
    )
    if result["status"] != "accepted":
        return ephemeral("Open SWE could not route that command to this code channel.")
    return ephemeral(f"Working on /{command}…")


@router.post("/webhooks/slack/interactivity")
async def slack_interactivity(
    request: common.Request, background_tasks: common.BackgroundTasks
) -> WebhookResponse | BlockSuggestionResponse | FeedbackResponse:
    """Handle Slack Block Kit interactions."""
    body = await request.body()
    _verify_signature(request, body, "interactivity")

    form = common.parse_qs(body.decode("utf-8"))
    payload_raw = (form.get("payload") or [""])[0]
    payload = parse_json_object(payload_raw.encode("utf-8"))
    if payload is None:
        common.logger.warning("Failed to parse Slack interactivity payload")
        return {"status": "error", "message": "Invalid payload"}
    if is_slack_feedback_payload(payload):
        return await handle_slack_feedback_interaction(payload, background_tasks)

    interaction = SlackInteraction.parse(payload)
    if interaction is None:
        return ignored("Invalid Slack interaction")

    if interaction.type == "block_suggestion" and interaction.container.type == "code_channel_view":
        if not (interaction.channel_id and interaction.container.view_id and interaction.action_id):
            return {"options": []}
        options = await common.get_block_suggestions(
            get_langgraph_client(),
            interaction.channel_id,
            interaction.container.view_id,
            interaction.action_id,
            interaction.value[:200],
        )
        return {"options": options}
    if interaction.type == "block_actions" and interaction.container.type == "code_channel_view":
        first_action = interaction.actions[0] if interaction.actions else None
        event_ts = (first_action.action_ts if first_action else "") or _synthetic_slack_ts()
        summary: JsonObject = {
            "view_id": interaction.container.view_id,
            "actions": [action.summary() for action in interaction.actions[:10]],
        }
        payload_fingerprint = hashlib.sha256(payload_raw.encode()).hexdigest()
        return await _queue_code_channel_turn(
            background_tasks,
            channel_id=interaction.channel_id,
            user_id=interaction.user.id,
            text=_bounded_payload_text(
                "A user interacted with a code channel Block Kit view.", summary
            ),
            event_id=f"code-channel-view:{interaction.trigger_id or payload_fingerprint}",
            event_ts=event_ts,
            explicit_request=True,
        )

    action = _first_option_action(interaction.actions)
    if action is None:
        return ignored("No Open SWE action")

    channel_id = interaction.channel_id
    if not channel_id:
        return ignored("Slack channel is not eligible")
    channel_context = await common.resolve_slack_channel_context(channel_id, use_cache=False)
    if not common.slack_channel_allows_operations(channel_context):
        common.logger.warning("Blocked Slack interaction in ineligible channel=%s", channel_id)
        return ignored("Slack channel is not eligible")

    button = SlackButtonValue.parse(parse_json_object((action.value or "{}").encode("utf-8")))
    if button is None:
        return ignored("Invalid action value")

    user_id = interaction.user.id
    action_ts = action.action_ts or interaction.message_ts
    thread_ts = interaction.thread_ts

    # From here on the interaction is addressed to Open SWE: any failure is reported to the thread.
    target = SlackRequestTarget(channel_id=channel_id, thread_ts=thread_ts or action_ts)

    async def dispatch() -> WebhookResponse:
        if button.type == "workflow_push_approval":
            if not channel_id or not thread_ts or not button.fingerprint:
                return ignored("Missing workflow approval context")

            thread_id = await common.lookup_slack_thread_id(
                get_langgraph_client(), channel_id, thread_ts
            )
            if not thread_id:
                return ignored("Slack thread is not associated")
            if button.action not in {"approve", "reject"}:
                return ignored("Unknown workflow approval action")
            approved = button.action == "approve"
            record = await common.decide_workflow_push_approval(
                thread_id, button.fingerprint, approved=approved, actor=user_id
            )
            if record is None:
                await common.post_slack_thread_reply(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    text="I couldn't find that workflow approval request. Trigger the push again to create a fresh approval.",
                    agent_thread_id=thread_id,
                )
                return ignored("workflow approval not found")
            background_tasks.add_task(
                _update_selected_option_message,
                interaction,
                action,
                "Approve workflow push" if approved else "Reject workflow push",
            )
            if not approved:
                await common.post_slack_thread_reply(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    text=f"Workflow push rejected for fingerprint `{button.fingerprint}`. No workflow files will be pushed.",
                    agent_thread_id=thread_id,
                )
                return accepted("Workflow push rejected")

            await common.post_slack_thread_reply(
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=f"Workflow push approved for fingerprint `{button.fingerprint}`. Open SWE will retry the blocked push.",
                agent_thread_id=thread_id,
            )
            repo = await common.get_slack_repo_config(
                channel_id,
                thread_ts,
                slack_user_id=user_id,
                channel_context=channel_context,
                thread_id=thread_id,
            )
            background_tasks.add_task(
                service.process_slack_mention,
                SlackRequest(
                    channel_id=channel_id,
                    channel_context=channel_context,
                    thread_ts=thread_ts,
                    event_ts=interaction.message.ts,
                    user_id=user_id,
                    text=(
                        "The workflow-file push approval was approved. Retry the blocked "
                        "git push now; do not alter workflow files before pushing."
                    ),
                    bot_user_id=common.SLACK_BOT_USER_ID,
                    thread_id=thread_id,
                ),
                repo,
            )
            return accepted("Workflow push approved, retry queued")

        if button.type == "plan_approval":
            if not channel_id or not thread_ts:
                return ignored("Missing Slack action context")

            thread_id = await common.lookup_slack_thread_id(
                get_langgraph_client(), channel_id, thread_ts
            )
            if not thread_id:
                return ignored("Slack thread is not associated")

            if button.action == "cancel":
                background_tasks.add_task(
                    _update_selected_option_message, interaction, action, "Cancel plan"
                )
                await common.post_slack_thread_reply(
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    text="Plan cancelled. No changes will be made.",
                    agent_thread_id=thread_id,
                )
                return accepted("Plan cancelled")

            if button.action == "approve":
                user_name = interaction.user.name or interaction.user.username or user_id
                background_tasks.add_task(
                    _update_selected_option_message, interaction, action, "Approve plan"
                )
                repo = await common.get_slack_repo_config(
                    channel_id, thread_ts, slack_user_id=user_id, channel_context=channel_context
                )
                background_tasks.add_task(
                    service.process_slack_plan_approval,
                    SlackRequest(
                        thread_id=thread_id,
                        channel_id=channel_id,
                        channel_context=channel_context,
                        thread_ts=thread_ts,
                        event_ts=interaction.message.ts,
                        user_id=user_id,
                        user_name=user_name,
                        text="approve",
                        bot_user_id=common.SLACK_BOT_USER_ID,
                    ),
                    repo,
                )
                return accepted("Plan approval queued")

            background_tasks.add_task(
                _update_selected_option_message, interaction, action, "Request plan changes"
            )
            return accepted("Reply to revise the plan")

        if button.type != "open_swe_option":
            return ignored("Unknown action type")

        response = button.response.strip()
        if not response:
            return ignored("Empty response")

        option_thread_ts = thread_ts or action_ts
        if not channel_id or not option_thread_ts or not action_ts or not user_id:
            return ignored("Missing Slack action context")

        thread_id = await common.lookup_slack_thread_id(
            get_langgraph_client(), channel_id, option_thread_ts
        )
        if not thread_id:
            return ignored("Slack thread is not associated")
        repo = await common.get_slack_repo_config(
            channel_id,
            option_thread_ts,
            slack_user_id=user_id,
            channel_context=channel_context,
            thread_id=thread_id,
        )
        background_tasks.add_task(_update_selected_option_message, interaction, action, response)
        background_tasks.add_task(
            service.process_slack_mention,
            SlackRequest(
                channel_id=channel_id,
                channel_context=channel_context,
                thread_ts=option_thread_ts,
                event_ts=action_ts,
                user_id=user_id,
                text=response,
                bot_user_id=common.SLACK_BOT_USER_ID,
                thread_id=thread_id,
            ),
            repo,
        )
        return accepted("Slack option queued")

    return await answer_slack_request(target, dispatch)


async def _update_selected_option_message(
    interaction: SlackInteraction, action: SlackBlockAction, fallback_label: str
) -> None:
    channel_id = interaction.channel_id
    message_ts = interaction.message_ts
    label = ((action.text.text if action.text else "") or fallback_label).strip()[:150]
    blocks = _selected_option_blocks(interaction.message, label)
    if not channel_id or not message_ts or not label or not blocks:
        return

    try:
        ok, error = await common.update_slack_message(
            channel_id,
            message_ts,
            interaction.message.text or label,
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


def _selected_option_blocks(message: SlackInteractionMessage, label: str) -> list[JsonObject]:
    selected_block: JsonObject = {
        "type": "context",
        "elements": [{"type": "plain_text", "text": f"Selected: {label}"}],
    }
    updated_blocks: list[JsonObject] = []
    replaced = False
    for block in message.blocks:
        elements = block.get("elements")
        if block.get("type") != "actions" or not _has_option_element(elements):
            updated_blocks.append(block)
            continue
        if not replaced:
            updated_blocks.append(selected_block)
            replaced = True
        if isinstance(elements, list):
            remaining = [element for element in elements if not _has_option_element([element])]
            if remaining:
                updated_blocks.append({**block, "elements": remaining})

    return updated_blocks if replaced else []


def _is_option_action_id(action_id: object) -> bool:
    return isinstance(action_id, str) and (
        action_id == _OPTION_ACTION_ID or action_id.startswith(f"{_OPTION_ACTION_ID}_")
    )


def _first_option_action(actions: list[SlackBlockAction]) -> SlackBlockAction | None:
    return next((action for action in actions if _is_option_action_id(action.action_id)), None)


def _has_option_element(elements: object) -> bool:
    """Whether a Block Kit ``elements`` list holds one of Open SWE's option buttons."""
    if not isinstance(elements, list):
        return False
    return any(
        isinstance(element, dict) and _is_option_action_id(element.get("action_id"))
        for element in elements
    )


@router.get("/webhooks/slack")
async def slack_webhook_verify() -> HealthResponse:
    """Verify endpoint for Slack webhook setup."""
    return {"status": "ok", "message": "Slack webhook endpoint is active"}
