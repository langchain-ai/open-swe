import asyncio
import contextvars
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agent.dashboard.options import ModelOption
from agent.input_messages import (
    dynamic_context_hash,
    human_input,
    input_message_text,
    input_message_timestamps,
    message_sender_id,
)
from agent.prompts import load_prompt, prompt
from agent.slack.code_channels import CODE_CHANNEL_SESSION_TS, is_code_channel, rename_session
from agent.source_context import SourceContext
from agent.transcript.mirror import mirror_thread_metadata

logger = logging.getLogger(__name__)

MAX_THREAD_TITLE_CHARS = 80
MAX_TITLE_INPUT_CHARS = 8_000
TITLE_GENERATION_MAX_TOKENS = 256
TITLE_GENERATION_TIMEOUT_SECONDS = 10
_background_tasks: set[asyncio.Task[None]] = set()
_inflight_thread_ids: set[str] = set()


class _ThreadTitle(BaseModel):
    title: str = Field(description="Concise, outcome-focused thread title, 3-8 words")


class ThreadHandoff(_ThreadTitle):
    requested_model: str | None = None
    unavailable_model: str | None = None


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


_TITLE_SYSTEM_PROMPT = load_prompt("thread-title.md")


def _thread_metadata(thread: Any) -> dict[str, Any]:
    if isinstance(thread, Mapping):
        metadata = thread.get("metadata")
    else:
        metadata = getattr(thread, "metadata", None)
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _title_input(messages: Sequence[BaseMessage]) -> str | None:
    texts: list[str] = []
    for message in messages:
        if dynamic_context_hash(message.content) is not None:
            continue
        text = (input_message_text(message.content) or message.text).strip()
        if text:
            texts.append(text)
    transcript = "\n\n".join(texts)
    return transcript[:MAX_TITLE_INPUT_CHARS] if transcript else None


def _normalize_title(title: str) -> str:
    normalized = " ".join(title.strip().strip("`\"'").split())
    normalized = " ".join(normalized.split()[:8]).rstrip(".")
    if len(normalized) <= MAX_THREAD_TITLE_CHARS:
        return normalized
    return normalized[:MAX_THREAD_TITLE_CHARS].rsplit(" ", 1)[0].rstrip()


async def generate_and_store_thread_title(
    *,
    thread_id: str,
    conversation: str,
    model: BaseChatModel,
    client: Any,
    requested_models: Mapping[str, ModelOption] | None = None,
) -> ThreadHandoff | None:
    thread = await client.threads.get(thread_id=thread_id)
    metadata = _thread_metadata(thread)
    expected_title = metadata.get("title")
    title_seed = metadata.get("title_seed")
    replace_title = (
        metadata.get("source") in {"dashboard", "slack"}
        and isinstance(title_seed, str)
        and expected_title == title_seed
    )
    if not replace_title and requested_models is None:
        return None

    structured = model.with_structured_output(
        ThreadHandoff if requested_models is not None else _ThreadTitle
    )
    system_prompt = _TITLE_SYSTEM_PROMPT
    if requested_models is not None:
        system_prompt += "\n\n" + prompt(
            "thread-model-handoff",
            available_models="\n".join(
                f"- {option['label']}: {model_id}" for model_id, option in requested_models.items()
            ),
        )
    title_input = human_input(
        conversation,
        {
            "sender_id": "person:title-subject",
            "surface": "automation",
            "kind": "human",
        },
    )["content"]
    if not isinstance(title_input, str):
        return
    async with asyncio.timeout(TITLE_GENERATION_TIMEOUT_SECONDS):
        result = await structured.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=title_input),
            ],
            # Empty callbacks, so this call cannot inherit the run's handlers and
            # stream its tokens into the thread the user is watching.
            config={"callbacks": [], "run_name": "thread-title"},
        )
    if not isinstance(result, _ThreadTitle):
        return
    handoff = result if isinstance(result, ThreadHandoff) else None
    title = _normalize_title(result.title)
    if not title or not replace_title:
        return handoff

    async def store_title() -> None:
        try:
            async with asyncio.timeout(TITLE_GENERATION_TIMEOUT_SECONDS):
                latest = await client.threads.get(thread_id=thread_id)
                latest_metadata = _thread_metadata(latest)
                if (
                    latest_metadata.get("title") != expected_title
                    or latest_metadata.get("title_seed") != title_seed
                ):
                    return
                await client.threads.update(
                    thread_id=thread_id,
                    metadata={"title": title, "title_seed": None},
                )
                await mirror_thread_metadata(thread_id, {"title": title})
                # A promotion to a code channel can race the title update.
                latest = await client.threads.get(thread_id=thread_id)
                context = SourceContext.from_metadata(_thread_metadata(latest))
                # DMs share the session timestamp but have no session name to set.
                if (
                    context.slack_location
                    and context.slack_location[1] == CODE_CHANNEL_SESSION_TS
                    and await is_code_channel(context.slack_location[0])
                ):
                    await rename_session(context.slack_location[0], title)
        except Exception:
            logger.warning(
                "Thread title persistence failed", extra={"thread_id": thread_id}, exc_info=True
            )

    if handoff is not None:
        # Title I/O must not consume the handoff timeout after inference succeeds.
        task = asyncio.create_task(store_title(), context=contextvars.Context())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    else:
        await store_title()
    return handoff


async def initial_thread_handoff(
    *,
    thread_id: str,
    messages: Sequence[BaseMessage],
    model: BaseChatModel,
    client: object,
    requested_models: Mapping[str, ModelOption],
    slack_event_ts: str | None = None,
) -> ThreadHandoff | None:
    conversation = original_human_task(messages, slack_event_ts=slack_event_ts)
    if conversation is None:
        return None
    try:
        async with asyncio.timeout(TITLE_GENERATION_TIMEOUT_SECONDS):
            return await asyncio.create_task(
                generate_and_store_thread_title(
                    thread_id=thread_id,
                    conversation=conversation[:MAX_TITLE_INPUT_CHARS],
                    model=model,
                    client=client,
                    requested_models=requested_models,
                ),
                context=contextvars.Context(),
            )
    except Exception:
        logger.warning("Initial title and model handoff failed", exc_info=True)
        return None


def schedule_thread_title_generation(
    *,
    thread_id: str,
    messages: Sequence[BaseMessage],
    model: BaseChatModel,
    client: Any,
) -> None:
    conversation = _title_input(messages)
    if conversation is None or thread_id in _inflight_thread_ids:
        return
    _inflight_thread_ids.add(thread_id)

    async def run() -> None:
        try:
            await generate_and_store_thread_title(
                thread_id=thread_id,
                conversation=conversation,
                model=model,
                client=client,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Thread title generation failed for %s: %s", thread_id, exc)
        finally:
            _inflight_thread_ids.discard(thread_id)

    # A fresh context, not the caller's: an inherited context carries LangGraph's
    # stream writer, and this call's structured-output chunks would then be
    # emitted into the run's message stream — rendering as a bogus assistant
    # message and derailing the client's assembly of every later chunk.
    task = asyncio.get_running_loop().create_task(run(), context=contextvars.Context())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
