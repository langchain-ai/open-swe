import json
import logging
from collections.abc import Mapping, Sequence
from typing import Literal

from agent.prompts import prompt
from agent.slack.request import SlackRequest
from agent.utils.jev import JevDecision, select_jev_choice

logger = logging.getLogger(__name__)

KitchenIntent = Literal["respond", "ignore", "uncertain"]


async def kitchen_message_intent(
    request: SlackRequest, messages: Sequence[Mapping[str, object]], *, bot_username: str
) -> KitchenIntent | None:
    if (
        not request.kitchen_channel
        or request.event_ts == request.thread_ts
        or request.explicit_request
        or request.message_update
        or request.context_thread_ts
        or request.attachments
        or len(request.text) > 6000
        or any(
            str(message.get("ts", "")) == request.event_ts and message.get("files")
            for message in messages
        )
        or (request.bot_user_id and f"<@{request.bot_user_id}>" in request.text)
        or (bot_username and f"@{bot_username}" in request.text)
    ):
        return None
    earlier = sorted(
        (message for message in messages if str(message.get("ts", "")) < request.event_ts),
        key=lambda message: str(message.get("ts", "")),
    )
    recent = earlier[:1] + earlier[-11:] if len(earlier) > 12 else earlier
    context = json.dumps(
        {
            "bot_user_id": request.bot_user_id,
            "bot_username": bot_username,
            "thread": [
                {key: str(message.get(key, ""))[:1500] for key in ("user", "bot_id", "text")}
                for message in recent
            ],
            "current_message": {"user": request.user_id, "text": request.text},
        }
    )
    decision = JevDecision()
    choice = await select_jev_choice(
        context,
        question="slack_intent",
        instructions=prompt("slack/intent-instructions"),
        criteria={
            name: prompt(f"slack/intent-{name}") for name in ("respond", "ignore", "uncertain")
        },
        decision=decision,
    )
    logger.info(
        "Classified kitchen message intent",
        extra={
            "slack_channel": request.channel_id,
            "slack_message_ts": request.event_ts,
            "intent": choice,
            "confidence": decision.confidence,
            "outcome": decision.outcome,
            "reason": decision.reason,
        },
    )
    if choice in {"respond", "ignore", "uncertain"} and (decision.confidence or 0) >= 0.9:
        if choice == "ignore" and any(
            len(str(message.get("text", ""))) > 1500 for message in recent
        ):
            return None
        return choice
    return None
