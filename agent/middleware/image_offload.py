"""Keep inline images out of the checkpointed conversation.

Pasted screenshots arrive as base64 image blocks inside human messages (and
``read_file`` results). Left in place they are copied into every ``messages``
snapshot and shipped on every state read, where a handful of screenshots
outweighs the whole visible conversation. This middleware moves each image
into the LangGraph store once and leaves a ``file_id`` reference in the
message; the bytes are swapped back in only for the provider call.
"""

import logging
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import TypedDict

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.messages import AnyMessage, BaseMessage
from langgraph.config import get_config, get_store
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore

from agent.middleware.trace import OpenSWEMiddleware
from agent.thread_images import IMAGE_STORE_NAMESPACE, image_owner_threads

logger = logging.getLogger(__name__)

IMAGE_UNAVAILABLE_TEXT = "[image no longer available]"
_REHYDRATE_CACHE_SIZE = 32

type ContentBlock = dict[str, object]


class StoredImage(TypedDict):
    thread_id: str
    mime_type: str
    base64: str
    file_name: str | None


def _as_block(item: object) -> ContentBlock | None:
    if isinstance(item, dict) and item.get("type") == "image":
        return item
    return None


def _inline_image(block: ContentBlock) -> tuple[str, str] | None:
    """``(base64, mime_type)`` for an image block that still carries its bytes."""
    encoded = block.get("base64")
    mime_type = block.get("mime_type")
    if isinstance(encoded, str) and encoded and isinstance(mime_type, str) and mime_type:
        return encoded, mime_type
    return None


def _reference_id(block: ContentBlock) -> str | None:
    file_id = block.get("file_id")
    if isinstance(file_id, str) and file_id and "base64" not in block:
        return file_id
    return None


def _has_inline_image(message: BaseMessage) -> bool:
    content = message.content
    return isinstance(content, list) and any(
        (block := _as_block(item)) is not None and _inline_image(block) is not None
        for item in content
    )


def _has_reference(message: BaseMessage) -> bool:
    content = message.content
    return isinstance(content, list) and any(
        (block := _as_block(item)) is not None and _reference_id(block) is not None
        for item in content
    )


def _resolve_store(runtime: Runtime | None) -> BaseStore | None:
    if runtime is not None and runtime.store is not None:
        return runtime.store
    try:
        return get_store()
    except RuntimeError:
        return None


def _configurable() -> dict[str, object]:
    try:
        configurable = get_config().get("configurable", {})
    except RuntimeError:
        return {}
    return configurable if isinstance(configurable, dict) else {}


async def offload_message_images(
    message: BaseMessage, store: BaseStore, thread_id: str
) -> BaseMessage | None:
    """A copy of ``message`` with its inline images stored and referenced.

    Returns ``None`` when the message carries no inline image.
    """
    if not _has_inline_image(message):
        return None
    content: list[object] = []
    for item in message.content:
        block = _as_block(item)
        inline = _inline_image(block) if block is not None else None
        if block is None or inline is None:
            content.append(item)
            continue
        encoded, mime_type = inline
        file_name = block.get("file_name")
        image_id = uuid.uuid4().hex
        stored: StoredImage = {
            "thread_id": thread_id,
            "mime_type": mime_type,
            "base64": encoded,
            "file_name": file_name if isinstance(file_name, str) else None,
        }
        await store.aput(IMAGE_STORE_NAMESPACE, image_id, dict(stored))
        reference: ContentBlock = {
            key: value for key, value in block.items() if key not in {"base64", "data", "url"}
        }
        reference["file_id"] = image_id
        content.append(reference)
    return message.model_copy(update={"content": content})


class ImageOffloadMiddleware(OpenSWEMiddleware):
    """Store inline images once and reference them from the conversation."""

    def __init__(self) -> None:
        super().__init__()
        self._rehydrated: OrderedDict[tuple[frozenset[str], str], ContentBlock | None] = (
            OrderedDict()
        )

    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, object] | None:
        messages = state.get("messages") or []
        if not any(_has_inline_image(message) for message in messages):
            return None
        store = _resolve_store(runtime)
        if store is None:
            logger.warning("No store available to offload inline images")
            return None
        thread_id = str(_configurable().get("thread_id") or "")
        replaced: list[BaseMessage] = []
        for message in messages:
            try:
                offloaded = await offload_message_images(message, store, thread_id)
            except Exception:  # noqa: BLE001
                # Keeping the bytes inline is the safe failure: the run still works.
                logger.warning(
                    "Could not offload inline image",
                    extra={"thread_id": thread_id, "message_id": message.id},
                    exc_info=True,
                )
                continue
            if offloaded is not None:
                replaced.append(offloaded)
        if not replaced:
            return None
        logger.info(
            "Offloaded inline images",
            extra={"thread_id": thread_id, "message_count": len(replaced)},
        )
        return {"messages": replaced}

    async def _inline_block(
        self, store: BaseStore, block: ContentBlock, file_id: str, owners: frozenset[str]
    ) -> ContentBlock:
        # The middleware instance is shared by every thread, so the cache is
        # scoped to the threads allowed to see the image.
        cache_key = (owners, file_id)
        if cache_key in self._rehydrated:
            self._rehydrated.move_to_end(cache_key)
            cached = self._rehydrated[cache_key]
        else:
            cached = None
            item = await store.aget(IMAGE_STORE_NAMESPACE, file_id)
            value = item.value if item is not None else None
            # A reference can be injected into a run's input, so the bytes are
            # only inlined when the image belongs to a thread this one may show.
            if isinstance(value, dict) and value.get("thread_id") in owners:
                encoded = value.get("base64")
                mime_type = value.get("mime_type")
                if isinstance(encoded, str) and isinstance(mime_type, str):
                    cached = {"type": "image", "base64": encoded, "mime_type": mime_type}
            self._rehydrated[cache_key] = cached
            while len(self._rehydrated) > _REHYDRATE_CACHE_SIZE:
                self._rehydrated.popitem(last=False)
        if cached is None:
            return {"type": "text", "text": IMAGE_UNAVAILABLE_TEXT}
        return {**{key: value for key, value in block.items() if key != "file_id"}, **cached}

    async def _rehydrate(
        self, store: BaseStore, message: AnyMessage, owners: frozenset[str]
    ) -> AnyMessage:
        content: list[object] = []
        for item in message.content:
            block = _as_block(item)
            file_id = _reference_id(block) if block is not None else None
            if block is None or file_id is None:
                content.append(item)
                continue
            content.append(await self._inline_block(store, block, file_id, owners))
        return message.model_copy(update={"content": content})

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if not any(_has_reference(message) for message in request.messages):
            return await handler(request)
        store = _resolve_store(request.runtime)
        if store is None:
            return await handler(request)
        configurable = _configurable()
        owners = image_owner_threads(
            str(configurable.get("thread_id") or ""), configurable.get("continued_from_thread_id")
        )
        messages: list[AnyMessage] = [
            await self._rehydrate(store, message, owners) if _has_reference(message) else message
            for message in request.messages
        ]
        return await handler(request.override(messages=messages))
