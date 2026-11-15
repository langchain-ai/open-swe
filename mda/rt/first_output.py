"""When an agent's model call opened its reply and first gave text, stamped on the reply.

The backend times a dashboard send from these stamps (``openswe.dashboard.ttft``):
the run's stream cannot, since a run the transcript dashboard starts streams no
messages to its thread, and the transcript only hears of a reply once it is whole.
"""

import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Final
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.callbacks.manager import AsyncCallbackManager
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables.config import ensure_config, patch_config, var_child_runnable_config
from runtime_api.loop import FIRST_OUTPUT_AT_KEY, FIRST_TEXT_AT_KEY

# Model calls no person sees stream, such as a classifier's.
_HIDDEN_TAGS: Final = frozenset({"nostream", "langsmith:hidden"})


class FirstOutput(AsyncCallbackHandler):
    """When the model calls made for one reply first streamed: their opening, and text.

    The opening is the first chunk or stream event, which a provider sends as it opens
    its message, thinking or a tool call included. Marks are kept per model call: one
    that fails, before a retry or a fallback, streamed nothing the reply keeps.
    """

    run_inline = True

    def __init__(self) -> None:
        # Per model call (its callback run id): when it opened, and its first text.
        self._calls: dict[UUID, tuple[int, int | None]] = {}

    @staticmethod
    def now_ms() -> int:
        return time.time_ns() // 1_000_000

    @property
    def output_at_ms(self) -> int | None:
        return min((opened for opened, _ in self._calls.values()), default=None)

    @property
    def text_at_ms(self) -> int | None:
        return min((text for _, text in self._calls.values() if text is not None), default=None)

    def _saw(self, run_id: UUID, tags: Sequence[str] | None, *, text: bool) -> None:
        if _HIDDEN_TAGS & set(tags or ()):
            return
        now = self.now_ms()
        opened, text_at = self._calls.get(run_id, (now, None))
        self._calls[run_id] = (opened, now if text and text_at is None else text_at)

    async def on_llm_new_token(
        self,
        token: str | list[str | dict[str, object]],
        *,
        chunk: object = None,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: object,
    ) -> None:
        message = getattr(chunk, "message", None)
        text = message.text if isinstance(message, BaseMessage) else token
        self._saw(run_id, tags, text=isinstance(text, str) and bool(text))

    async def on_stream_event(
        self,
        event: Mapping[str, object],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: object,
    ) -> None:
        delta = event.get("delta") if event.get("event") == "content-block-delta" else None
        text = delta.get("text") if isinstance(delta, Mapping) else None
        self._saw(run_id, tags, text=isinstance(text, str) and bool(text))

    async def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: object,
    ) -> None:
        # A failed attempt's output was superseded; the reply is the next attempt's.
        self._calls.pop(run_id, None)

    @contextmanager
    def watching(self) -> Iterator[None]:
        """Hear the model calls made inside this block."""
        config = ensure_config()
        callbacks = AsyncCallbackManager.configure(config.get("callbacks"))
        callbacks.add_handler(self, inherit=True)
        token = var_child_runnable_config.set(patch_config(config, callbacks=callbacks))
        try:
            yield
        finally:
            var_child_runnable_config.reset(token)

    def stamp(self, replies: Sequence[BaseMessage]) -> None:
        """Stamp each reply with when its call opened and first gave text, now if it
        streamed none."""
        now = self.now_ms()
        for reply in replies:
            if not isinstance(reply, AIMessage):
                continue
            output_at = self.output_at_ms or now
            text_at = (self.text_at_ms or now) if reply.text else None
            reply.response_metadata = {
                **reply.response_metadata,
                FIRST_OUTPUT_AT_KEY: output_at,
                **({FIRST_TEXT_AT_KEY: max(text_at, output_at)} if text_at is not None else {}),
            }
