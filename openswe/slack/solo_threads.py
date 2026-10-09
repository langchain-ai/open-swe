"""Mention-free follow-ups in Slack threads Open SWE was tagged in.

Solo threads route every follow-up; shared threads ask a decision model whether
the newest message is addressed to Open SWE.
"""

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Literal

from langgraph_sdk.client import LangGraphClient

from openswe.config import ENV
from openswe.prompts import prompt
from openswe.slack.client import slack_thread_mutation_lock
from openswe.slack.events import claim_slack_event
from openswe.slack.http import SlackClient
from openswe.slack.payloads import SlackMessage
from openswe.utils.gateway import gateway_base_url
from openswe.utils.json_types import JsonObject

if TYPE_CHECKING:
    from langchain_openai.decisions import OpenAIDecisions

logger = logging.getLogger(__name__)

_NAMESPACE = "slack_solo_threads"
_MAX_HISTORY_PAGES = 5
_DECISION_MODEL = "gpt-6-luna"
_DECISION_TIMEOUT_SECONDS = 3.0
_DECISION_THRESHOLD = 0.8
_DECISION_CONTEXT_MESSAGES = 20

Participation = Literal["solo", "shared", "unknown"]


async def _thread_messages(channel_id: str, thread_ts: str) -> list[SlackMessage] | None:
    """Every message in the thread, or ``None`` when history cannot be read completely."""
    cursor = ""
    seen_cursors: set[str] = set()
    collected: list[SlackMessage] = []
    async with SlackClient.bot() as slack:
        for _ in range(_MAX_HISTORY_PAGES):
            page = await slack.conversations_replies(
                channel=channel_id, ts=thread_ts, limit=200, cursor=cursor
            )
            messages = page.get("messages")
            if not isinstance(messages, list) or not messages:
                return None
            for raw in messages:
                message = SlackMessage.parse(raw)
                if message is None:
                    return None
                collected.append(message)
            metadata = page.get("response_metadata", {})
            cursor = metadata.get("next_cursor", "") if isinstance(metadata, dict) else ""
            if not cursor:
                complete = not page.get("has_more") and any(m.ts == thread_ts for m in collected)
                return collected if complete else None
            if not isinstance(cursor, str) or cursor in seen_cursors:
                return None
            seen_cursors.add(cursor)
    return None


def _thread_is_solo(messages: list[SlackMessage], user_id: str) -> bool | None:
    for message in messages:
        if message.is_from_bot or message.is_noise:
            continue
        if not message.user:
            return None
        if message.user != user_id:
            return False
    return True


def _decisions_client() -> OpenAIDecisions | None:
    from langchain_openai.decisions import OpenAIDecisions

    if openai_key := ENV.OPENAI_API_KEY.optional():
        return OpenAIDecisions(model=_DECISION_MODEL, api_key=openai_key)
    gateway_key = ENV.LANGSMITH_GATEWAY_API_KEY.optional() or ENV.LANGSMITH_API_KEY.optional()
    if not gateway_key:
        return None
    return OpenAIDecisions(
        model=_DECISION_MODEL, api_key=gateway_key, base_url=f"{gateway_base_url()}/openai/v1"
    )


def _transcript(messages: list[SlackMessage], bot_user_id: str) -> str:
    def speaker(message: SlackMessage) -> str:
        if message.user == bot_user_id:
            return "Open SWE"
        return "another bot" if message.is_from_bot else f"<@{message.user}>"

    mention = f"<@{bot_user_id}>"
    return json.dumps(
        [
            {"from": speaker(m), "text": (m.text or "").replace(mention, "@Open SWE")}
            for m in messages[-_DECISION_CONTEXT_MESSAGES:]
            if not m.is_noise
        ]
    )


async def _addressed_to_open_swe(
    channel_id: str, messages: list[SlackMessage], message_ts: str, bot_user_id: str
) -> bool:
    """Whether the newest message in a shared thread is meant for Open SWE; fails closed."""
    from langchain_openai.decisions import Predicate

    decisions = _decisions_client()
    if decisions is None or messages[-1].ts != message_ts:
        return False
    # Slack retries slow acks; classify each message once so retries never re-ask or flip.
    if not await claim_slack_event(f"addressed-followup:{channel_id}:{message_ts}"):
        return False
    async with asyncio.timeout(_DECISION_TIMEOUT_SECONDS):
        response = await decisions.ainvoke(
            {
                "input": _transcript(messages, bot_user_id),
                "questions": {
                    "addressed": Predicate(instructions=prompt("slack/addressed-followup"))
                },
            },
            config={"tags": ["nostream"]},
        )
    answer = response.predicates.get("addressed")
    return answer is not None and answer.probability >= _DECISION_THRESHOLD


async def _track_participation(
    client: LangGraphClient,
    *,
    channel_id: str,
    thread_ts: str,
    message_ts: str,
    user_id: str,
    explicit_mention: bool,
    messages: list[SlackMessage] | None,
) -> Participation:
    namespace = (_NAMESPACE, channel_id)
    timestamp = float(message_ts)
    async with (
        asyncio.timeout(2),
        slack_thread_mutation_lock(client, channel_id, thread_ts, purpose="solo-followups"),
    ):
        item = await client.store.get_item(namespace, thread_ts)
        value = item.get("value", {}) if item else {}
        if value.get("blocked"):
            return "shared"
        owner = value.get("user_id")
        if owner and owner != user_id:
            await client.store.put_item(namespace, thread_ts, {"blocked": True})
            return "shared"
        last_seen = value.get("last_seen")
        if not explicit_mention and (
            not isinstance(last_seen, (int, float)) or timestamp < last_seen
        ):
            return "unknown"
        solo = None if messages is None else _thread_is_solo(messages, user_id)
        if solo is False:
            await client.store.put_item(namespace, thread_ts, {"blocked": True})
            return "shared"
        if solo is None:
            logger.warning(
                "Slack solo-thread history is incomplete",
                extra={"slack_channel_id": channel_id, "slack_thread_ts": thread_ts},
            )
            return "unknown"
        updated: JsonObject = {
            "user_id": user_id,
            "last_seen": max(timestamp, last_seen)
            if isinstance(last_seen, (int, float))
            else timestamp,
        }
        await client.store.put_item(namespace, thread_ts, updated)
        return "solo"


async def allow_solo_thread_followup(
    client: LangGraphClient,
    *,
    channel_id: str,
    thread_ts: str,
    message_ts: str,
    user_id: str,
    bot_user_id: str,
    explicit_mention: bool,
) -> bool:
    """Track human participation and fail closed when follow-up routing cannot be verified."""
    try:
        if not explicit_mention and not await client.store.get_item(
            (_NAMESPACE, channel_id), thread_ts
        ):
            return False
        messages = await _thread_messages(channel_id, thread_ts)
        participation = await _track_participation(
            client,
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_ts=message_ts,
            user_id=user_id,
            explicit_mention=explicit_mention,
            messages=messages,
        )
        if explicit_mention or participation == "unknown" or not messages:
            return False
        if participation == "solo":
            return True
        return await _addressed_to_open_swe(channel_id, messages, message_ts, bot_user_id)
    except Exception:
        logger.warning(
            "Could not verify Slack thread follow-up routing",
            extra={"slack_channel_id": channel_id, "slack_thread_ts": thread_ts},
            exc_info=True,
        )
        return False
