"""Stored Slack intake forms and attributed modal submissions."""

import json
import logging
from time import time_ns
from uuid import uuid4

from fastapi import BackgroundTasks
from pydantic import BaseModel, Field

from agent.slack import webhook
from agent.slack.blocks import (
    actions,
    block_payload,
    button,
    checkboxes,
    modal,
    section,
    text_input,
    view_payload,
)
from agent.slack.client import open_slack_modal, post_slack_thread_reply_with_ts
from agent.slack.dm import CONCIERGE_TS, is_dm_channel
from agent.slack.payloads import (
    SlackBlockAction,
    SlackButtonValue,
    SlackInteraction,
    SlackViewSubmission,
)
from agent.slack.request import SlackRequest
from agent.slack.responses import FeedbackResponse, WebhookResponse, ignored
from agent.store import TypedStore
from agent.users import User
from agent.utils.thread_ops import langgraph_client
from agent.webhooks import common

logger = logging.getLogger(__name__)
CALLBACK_ID = "open_swe_form"
ACTION_ID = "open_swe_form_open"


class FormItem(BaseModel):
    label: str = Field(min_length=1, max_length=75)
    comment: bool = False


class FormRecord(BaseModel):
    id: str
    title: str
    items: list[FormItem]
    channel_id: str
    thread_ts: str
    thread_id: str
    user_id: str
    reply_thread_ts: str = ""


def _store(channel_id: str) -> TypedStore[FormRecord]:
    return TypedStore(("slack_forms", channel_id), FormRecord)


def is_form_submission(payload: dict[str, object]) -> bool:
    view = payload.get("view")
    return (
        payload.get("type") in {"view_submission", "view_closed"}
        and isinstance(view, dict)
        and view.get("callback_id") == CALLBACK_ID
    )


async def handle_button(
    interaction: SlackInteraction, action: SlackBlockAction, button_value: SlackButtonValue
) -> WebhookResponse | FeedbackResponse:
    channel_id = interaction.channel_id
    record = (
        await _store(channel_id).get(button_value.fingerprint)
        if channel_id and button_value.fingerprint
        else None
    )
    if not record or record.channel_id != channel_id or record.user_id != interaction.user.id:
        return ignored("Form unavailable")
    context = await common.resolve_slack_channel_context(channel_id, use_cache=False)
    if not context.allows_operations or not interaction.trigger_id:
        return ignored("Form unavailable")
    thread_ts = (
        CONCIERGE_TS
        if is_dm_channel(context) and await User.concierge_mode_for_slack(interaction.user.id)
        else interaction.thread_ts
    )
    if (
        thread_ts != record.thread_ts
        or await common.lookup_slack_thread_id(langgraph_client(), channel_id, thread_ts)
        != record.thread_id
    ):
        return ignored("Form thread unavailable")
    blocks = [
        checkboxes(
            block_id="choices",
            label="Select items",
            action_id="selected",
            options=[item.label for item in record.items],
            optional=True,
        )
    ]
    blocks.extend(
        text_input(
            block_id=f"comment_{index}",
            label=f"Comment: {item.label}",
            action_id="comment",
            multiline=True,
            max_length=1000,
            optional=True,
        )
        for index, item in enumerate(record.items)
        if item.comment
    )
    view = modal(
        callback_id=CALLBACK_ID,
        title=record.title,
        blocks=blocks,
        submit="Submit",
        close="Cancel",
        private_metadata=json.dumps({"channel_id": channel_id, "form_id": record.id}),
    )
    if not await open_slack_modal(interaction.trigger_id, view_payload(view)):
        return ignored("Could not open form")
    return {}


async def handle_submission(
    payload: SlackViewSubmission, background_tasks: BackgroundTasks
) -> FeedbackResponse:
    if payload.type == "view_submission":
        background_tasks.add_task(_dispatch, payload)
    return {}


async def _dispatch(payload: SlackViewSubmission) -> None:
    metadata = payload.metadata
    channel_id, form_id = metadata.get("channel_id"), metadata.get("form_id")
    if not isinstance(channel_id, str) or not isinstance(form_id, str) or not payload.user.id:
        return
    record = await _store(channel_id).get(form_id)
    if (
        not record
        or record.id != form_id
        or record.channel_id != channel_id
        or record.user_id != payload.user.id
    ):
        return
    context = await common.resolve_slack_channel_context(channel_id, use_cache=False)
    if not context.allows_operations:
        return
    try:
        mapped = await common.lookup_slack_thread_id(
            langgraph_client(), channel_id, record.thread_ts
        )
    except common.SlackThreadMappingError:
        logger.warning("Form thread mapping unavailable", extra={"channel_id": channel_id})
        return
    if mapped != record.thread_id:
        return
    selected = payload.selected("choices", "selected")
    if len(selected) != len(set(selected)) or any(
        not value.isascii() or not value.isdecimal() or int(value) >= len(record.items)
        for value in selected
    ):
        return
    lines = [f"Form submitted: {record.title}", "Selected items:"]
    for value in selected:
        index = int(value)
        lines.append(f"- {record.items[index].label}")
        if record.items[index].comment and (
            comment := payload.submitted(f"comment_{index}", "comment").strip()
        ):
            lines.append(f"  Comment: {comment[:1000]}")
    if not selected:
        lines.append("(none)")
    repo = await common.get_slack_repo_config(
        channel_id,
        record.thread_ts,
        slack_user_id=payload.user.id,
        channel_context=context,
        thread_id=record.thread_id,
    )
    timestamp = time_ns()
    event_ts = f"{timestamp // 1_000_000_000}.{timestamp % 1_000_000_000:09d}"
    await webhook.process_slack_mention(
        SlackRequest(
            channel_id=channel_id,
            channel_context=context,
            thread_ts=record.thread_ts,
            event_ts=event_ts,
            event_id=f"form:{payload.view.id}",
            user_id=payload.user.id,
            text="\n".join(lines),
            bot_user_id=common.SLACK_BOT_USER_ID,
            thread_id=record.thread_id,
            concierge_mode=is_dm_channel(context) and record.thread_ts == CONCIERGE_TS,
            reply_thread_ts=record.reply_thread_ts,
        ),
        repo,
    )


async def create_form(
    title: str,
    items: list[FormItem],
    channel_id: str,
    thread_ts: str,
    thread_id: str,
    user_id: str,
    reply_thread_ts: str = "",
) -> tuple[FormRecord, str | None]:
    record = FormRecord(
        id=uuid4().hex,
        title=title,
        items=items,
        channel_id=channel_id,
        thread_ts=thread_ts,
        thread_id=thread_id,
        user_id=user_id,
        reply_thread_ts=reply_thread_ts,
    )
    await _store(channel_id).put(record.id, record)
    blocks = block_payload(
        [
            section(title),
            actions(
                button(
                    "Open form",
                    action_id=ACTION_ID,
                    value=json.dumps({"type": CALLBACK_ID, "fingerprint": record.id}),
                )
            ),
        ]
    )
    message_ts, _ = await post_slack_thread_reply_with_ts(
        channel_id, reply_thread_ts or thread_ts, title, blocks=blocks, agent_thread_id=thread_id
    )
    return record, message_ts
