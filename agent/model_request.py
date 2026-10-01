import asyncio
import contextvars
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage
from langsmith import get_current_run_tree, trace, tracing_context
from langsmith.run_trees import RunTree

from agent.dashboard.options import ModelOption
from agent.input_messages import input_message_text, input_message_timestamps, message_sender_id
from agent.prompts import prompt
from agent.utils.jev import JevDecision, select_jev_choice

MAX_MODEL_REQUEST_CHARS = 8_000


@dataclass(frozen=True)
class ModelRequestIntent:
    requested_model: str | None = None
    unavailable_model: bool = False


@dataclass
class ModelSelectionDecision:
    classifier: JevDecision = field(default_factory=JevDecision)
    requested_model: str | None = None
    outcome: Literal[
        "not_classified",
        "no_request",
        "accepted_request",
        "unavailable_request",
        "incompatible_request",
        "low_confidence",
        "classifier_failure",
        "reused_saved_choice",
        "selection_failure",
        "persistence_failure",
    ] = "not_classified"
    reason: str = "opening_classification_not_enabled"
    pin_persisted: bool = False


@asynccontextmanager
async def model_selection_trace() -> AsyncIterator[RunTree]:
    parent = get_current_run_tree() or RunTree.from_runnable_config(None)
    safe_parent = (
        parent.model_copy(update={"extra": {"metadata": {}}, "tags": []}) if parent else None
    )
    with tracing_context(parent=safe_parent or False, metadata={}, tags=[]):
        async with trace(
            "Model selection decision",
            parent=safe_parent,
            inputs={},
            exceptions_to_handle=(BaseException,),
        ) as span:
            yield span


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
    decision: JevDecision | None = None,
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
            decision=decision,
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
