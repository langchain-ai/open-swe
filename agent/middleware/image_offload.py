"""Keep inline images out of the checkpointed conversation.

Pasted screenshots arrive as base64 image blocks inside human messages (and
``read_file`` results). Left in place they are copied into every ``messages``
snapshot and shipped on every state read, where a handful of screenshots
outweighs the whole visible conversation. This middleware writes each image to
the thread's image store once (its sandbox, or the desktop artifacts directory)
and leaves a ``file_id`` reference in the message; the bytes are swapped back
in only for the provider call.
"""

import base64
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Self

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.messages import AnyMessage, BaseMessage
from langgraph.runtime import Runtime

from agent.desktop import is_desktop_run
from agent.middleware.trace import OpenSWEMiddleware
from agent.run_config import RunConfig
from agent.thread_images import (
    ContentBlock,
    ImageStore,
    as_image_block,
    has_inline_image,
    image_file_name,
    image_owner_threads,
    image_reference,
    offload_image_blocks,
    open_image_store,
)

logger = logging.getLogger(__name__)

IMAGE_UNAVAILABLE_TEXT = "[image no longer available]"
_REHYDRATE_CACHE_SIZE = 32


@dataclass(frozen=True)
class ImageRunContext:
    thread_id: str
    desktop: bool
    owners: tuple[str, ...]

    @classmethod
    def from_runtime(cls) -> Self | None:
        try:
            cfg = RunConfig.from_runtime()
        except RuntimeError:
            return None
        if not cfg.thread_id:
            return None
        return cls(
            thread_id=cfg.thread_id,
            desktop=is_desktop_run(cfg),
            owners=image_owner_threads(cfg.thread_id, cfg.continued_from_thread_id),
        )


def _has_inline_image(message: BaseMessage) -> bool:
    return has_inline_image(message.content)


def _has_reference(message: BaseMessage) -> bool:
    content = message.content
    return isinstance(content, list) and any(
        (block := as_image_block(item)) is not None and image_reference(block) is not None
        for item in content
    )


async def offload_message_images(message: BaseMessage, store: ImageStore) -> BaseMessage | None:
    """A copy of ``message`` with its inline images stored and referenced.

    Returns ``None`` when nothing was offloaded.
    """
    if not _has_inline_image(message):
        return None
    content = await offload_image_blocks(list(message.content), store)
    if content is None:
        return None
    return message.model_copy(update={"content": content})


class ImageOffloadMiddleware(OpenSWEMiddleware):
    """Store inline images once and reference them from the conversation."""

    def __init__(self) -> None:
        super().__init__()
        self._rehydrated: OrderedDict[tuple[tuple[str, ...], str], ContentBlock | None] = (
            OrderedDict()
        )

    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, object] | None:
        messages = state.get("messages") or []
        if not any(_has_inline_image(message) for message in messages):
            return None
        context = ImageRunContext.from_runtime()
        if context is None:
            logger.warning("No thread context available to offload inline images")
            return None
        try:
            store = await open_image_store(context.thread_id, desktop=context.desktop)
        except Exception:  # noqa: BLE001
            # Keeping the bytes inline is the safe failure: the run still works.
            logger.warning(
                "Could not open the image store",
                extra={"thread_id": context.thread_id},
                exc_info=True,
            )
            return None
        replaced: list[BaseMessage] = []
        for message in messages:
            try:
                offloaded = await offload_message_images(message, store)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Could not offload inline image",
                    extra={"thread_id": context.thread_id, "message_id": message.id},
                    exc_info=True,
                )
                continue
            if offloaded is not None:
                replaced.append(offloaded)
        if not replaced:
            return None
        logger.info(
            "Offloaded inline images",
            extra={"thread_id": context.thread_id, "message_count": len(replaced)},
        )
        return {"messages": replaced}

    async def _load(
        self, stores: dict[str, ImageStore], context: ImageRunContext, name: str
    ) -> bytes | None:
        # A reference can be injected into a run's input, so bytes are only
        # read from stores of threads this one may show.
        for owner in context.owners:
            store = stores.get(owner)
            if store is None:
                try:
                    store = await open_image_store(owner, desktop=context.desktop)
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "Could not open the image store",
                        extra={"thread_id": owner},
                        exc_info=True,
                    )
                    continue
                stores[owner] = store
            try:
                content = await store.get(name)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Could not read a stored image",
                    extra={"thread_id": owner, "image_name": name},
                    exc_info=True,
                )
                continue
            if content is not None:
                return content
        return None

    async def _inline_block(
        self,
        stores: dict[str, ImageStore],
        context: ImageRunContext,
        block: ContentBlock,
        file_id: str,
        mime_type: str,
    ) -> ContentBlock:
        # The middleware instance is shared by every thread, so the cache is
        # scoped to the threads allowed to see the image.
        cache_key = (context.owners, file_id)
        if cache_key in self._rehydrated:
            self._rehydrated.move_to_end(cache_key)
            cached = self._rehydrated[cache_key]
        else:
            cached = None
            name = image_file_name(file_id, mime_type)
            content = await self._load(stores, context, name) if name is not None else None
            if content is not None:
                cached = {
                    "type": "image",
                    "base64": base64.b64encode(content).decode("ascii"),
                    "mime_type": mime_type,
                }
            self._rehydrated[cache_key] = cached
            while len(self._rehydrated) > _REHYDRATE_CACHE_SIZE:
                self._rehydrated.popitem(last=False)
        if cached is None:
            return {"type": "text", "text": IMAGE_UNAVAILABLE_TEXT}
        return {**{key: value for key, value in block.items() if key != "file_id"}, **cached}

    async def _rehydrate(
        self, stores: dict[str, ImageStore], context: ImageRunContext, message: AnyMessage
    ) -> AnyMessage:
        content: list[object] = []
        for item in message.content:
            block = as_image_block(item)
            reference = image_reference(block) if block is not None else None
            if block is None or reference is None:
                content.append(item)
                continue
            content.append(await self._inline_block(stores, context, block, *reference))
        return message.model_copy(update={"content": content})

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if not any(_has_reference(message) for message in request.messages):
            return await handler(request)
        context = ImageRunContext.from_runtime()
        if context is None:
            return await handler(request)
        stores: dict[str, ImageStore] = {}
        messages: list[AnyMessage] = [
            await self._rehydrate(stores, context, message) if _has_reference(message) else message
            for message in request.messages
        ]
        return await handler(request.override(messages=messages))
