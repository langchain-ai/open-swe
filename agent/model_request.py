import asyncio
import contextvars
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from langchain_core.messages import BaseMessage, HumanMessage

from agent.dashboard.options import ModelOption
from agent.input_messages import input_message_text, input_message_timestamps, message_sender_id
from agent.prompts import prompt
from agent.utils.jev import select_jev_choice

MAX_MODEL_REQUEST_CHARS = 8_000


@dataclass(frozen=True)
class ModelRequestIntent:
    requested_model: str | None = None
    unavailable_model: bool = False


def original_human_task(
    messages: Sequence[BaseMessage], *, slack_event_ts: str | None = None
) -> str | None:
    for message in messages:
        if not isinstance(message, HumanMessage):
            continue
        if slack_event_ts and slack_event_ts not in input_message_timestamps(message.content):
            continue
        if message_sender_id(message.content, kind="human") is not None:
            return input_message_text(message.content)
        text = message.text
        if "<dynamic-context" not in text and "<input-message" not in text and text.strip():
            return text
    return None


async def infer_requested_model(
    *,
    messages: Sequence[BaseMessage],
    requested_models: Mapping[str, ModelOption],
    slack_event_ts: str | None = None,
) -> ModelRequestIntent | None:
    task = original_human_task(messages, slack_event_ts=slack_event_ts)
    if not task:
        return ModelRequestIntent()
    criteria = {
        model_id: prompt("model-request/available", label=option["label"], model_id=model_id)
        for model_id, option in requested_models.items()
    }
    criteria.update(
        no_request=prompt("model-request/no-request"),
        unavailable=prompt("model-request/unavailable"),
    )
    choice = await asyncio.create_task(
        select_jev_choice(
            task[:MAX_MODEL_REQUEST_CHARS],
            question="runtime_model",
            instructions=prompt("model-request/instructions"),
            criteria=criteria,
        ),
        context=contextvars.Context(),
    )
    if choice is None:
        return None
    if choice == "no_request":
        return ModelRequestIntent()
    if choice == "unavailable":
        return ModelRequestIntent(unavailable_model=True)
    return ModelRequestIntent(requested_model=choice)
