import asyncio
import contextvars
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph_sdk import get_client
from pydantic import BaseModel, Field

from agent.desktop import is_desktop_run
from agent.desktop_branch import rename_temporary_branch_to
from agent.input_messages import dynamic_context_hash, human_input, input_message_text
from agent.prompts import load_prompt
from agent.run_config import RunConfig
from agent.slack.code_channels import CODE_CHANNEL_SESSION_TS, rename_session
from agent.source_context import SourceContext

logger = logging.getLogger(__name__)

MAX_THREAD_TITLE_CHARS = 80
MAX_TITLE_INPUT_CHARS = 8_000
TITLE_GENERATION_MAX_TOKENS = 256
TITLE_GENERATION_TIMEOUT_SECONDS = 10
_background_tasks: set[asyncio.Task[None]] = set()
_inflight_thread_ids: set[str] = set()


class _ThreadTitle(BaseModel):
    title: str = Field(description="Concise, outcome-focused thread title, 3-8 words")


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


def _awaiting_generated_title(metadata: Mapping[str, Any]) -> bool:
    """A seeded title is the placeholder the UI shows until a real one lands."""
    title_seed = metadata.get("title_seed")
    return (
        metadata.get("source") in {"dashboard", "slack"}
        and isinstance(title_seed, str)
        and metadata.get("title") == title_seed
    )


async def store_thread_title(*, thread_id: str, title: str, client: Any) -> str | None:
    """Replace the seeded placeholder title, leaving user-chosen titles alone."""
    thread = await client.threads.get(thread_id=thread_id)
    metadata = _thread_metadata(thread)
    if not _awaiting_generated_title(metadata):
        return None
    normalized = _normalize_title(title)
    if not normalized:
        return None
    await client.threads.update(
        thread_id=thread_id,
        metadata={"title": normalized, "title_seed": None},
    )
    # Re-read after the update: the pre-update snapshot can be stale if the
    # thread was promoted to a code channel between the check and the update.
    latest = await client.threads.get(thread_id=thread_id)
    context = SourceContext.from_metadata(_thread_metadata(latest))
    if context.slack_location and context.slack_location[1] == CODE_CHANNEL_SESSION_TS:
        await rename_session(context.slack_location[0], normalized)
    return normalized


async def name_thread(*, thread_id: str, title: str, cfg: RunConfig) -> None:
    """Apply an agent-chosen title to the thread and, on desktop, its worktree branch."""
    try:
        await store_thread_title(thread_id=thread_id, title=title, client=get_client())
    except Exception:  # noqa: BLE001
        logger.warning("Storing agent-chosen thread title failed", extra={"thread": thread_id})
    if is_desktop_run(cfg) and cfg.local_project_path:
        try:
            await rename_temporary_branch_to(worktree_path=cfg.local_project_path, name=title)
        except Exception:  # noqa: BLE001
            logger.warning("Renaming worktree branch failed", extra={"thread": thread_id})


async def generate_and_store_thread_title(
    *,
    thread_id: str,
    conversation: str,
    model: BaseChatModel,
    client: Any,
) -> None:
    thread = await client.threads.get(thread_id=thread_id)
    metadata = _thread_metadata(thread)
    if not _awaiting_generated_title(metadata):
        return

    structured = model.with_structured_output(_ThreadTitle)
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
                SystemMessage(content=_TITLE_SYSTEM_PROMPT),
                HumanMessage(content=title_input),
            ],
            # Empty callbacks, so this call cannot inherit the run's handlers and
            # stream its tokens into the thread the user is watching.
            config={"callbacks": [], "run_name": "thread-title"},
        )
    if not isinstance(result, _ThreadTitle):
        return
    await store_thread_title(thread_id=thread_id, title=result.title, client=client)


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
