"""A model call's streaming deltas, batched into transcript fragments.

Deltas are buffered per message and flushed in paragraph mode: at a markdown
paragraph boundary once ``PARAGRAPH_FLUSH_SECONDS`` have passed since the last
flush, or at ``HARD_FLUSH_CHARS`` regardless. The backend's transcript
middleware and a remote runtime's capture alike; they differ only in where a
fragment goes.

The subagent *namespace* of the events emitted right now travels in a
``ContextVar``: it only ever flows downward, into the nested graph a ``task``
tool call runs inside its own coroutine.
"""

import logging
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final, Literal
from uuid import UUID, uuid4

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.callbacks.manager import AsyncCallbackManager, CallbackManager
from langchain_core.messages import BaseMessage
from langgraph.config import get_config

from openswe_transcript.wire import Fragment

logger = logging.getLogger(__name__)

PARAGRAPH_FLUSH_SECONDS: Final = 0.4
HARD_FLUSH_CHARS: Final = 24_000
# Model calls a middleware makes for its own bookkeeping (routing classifier,
# conversation offloading) are tagged out of the user-facing stream. Their
# tokens must not become transcript fragments.
_HIDDEN_TAGS: Final = frozenset({"nostream", "langsmith:hidden"})

_FENCE: Final = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LIST_ITEM: Final = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])\s")

_namespace: ContextVar[tuple[str, ...]] = ContextVar("transcript_namespace", default=())


def current_namespace() -> list[str]:
    """The subagent namespace for events emitted right now (``[]`` at the root)."""
    return list(_namespace.get())


@contextmanager
def tool_namespace(tool_call_id: str) -> Iterator[None]:
    """Emit the block's events under ``tool_call_id``, as a ``task`` call's subagent does."""
    token = _namespace.set((*_namespace.get(), tool_call_id))
    try:
        yield
    finally:
        _namespace.reset(token)


def message_text(message: BaseMessage) -> str:
    try:
        return message.text
    except Exception:
        logger.debug("Could not read message text", exc_info=True)
        return ""


def reasoning_text(message: BaseMessage) -> str:
    """Concatenated reasoning. Never stripped: a streaming chunk's leading space
    is part of the delta."""
    try:
        blocks = message.content_blocks
    except Exception:
        logger.debug("Could not read content blocks for reasoning", exc_info=True)
        return ""
    out = ""
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        if block.get("type") == "reasoning":
            reasoning = block.get("reasoning")
            if isinstance(reasoning, str):
                out += reasoning
        elif block.get("type") == "non_standard":
            value = block.get("value")
            if isinstance(value, Mapping) and value.get("type") == "thinking":
                thinking = value.get("thinking")
                if isinstance(thinking, str):
                    out += thinking
    return out


def paragraph_boundary(text: str) -> int:
    """The largest safe markdown flush offset in ``text`` (0 when there is none).

    Safe boundaries are the end of a blank line, the end of a closing code
    fence, and the start of a list item; offsets inside an open fence never
    qualify. The trailing partial line is never flushed, but a boundary in
    front of it is one once it has declared itself a list item.
    """
    best = 0
    offset = 0
    in_fence = False
    fence = ""
    lines = text.split("\n")
    for line in lines[:-1]:
        length = len(line) + 1
        match = _FENCE.match(line)
        if in_fence:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
                in_fence = False
                fence = ""
                best = offset + length
        elif match:
            in_fence = True
            fence = match.group(1)
        elif not line.strip():
            best = offset + length
        elif offset and _LIST_ITEM.match(line):
            best = offset
        offset += length
    if offset and not in_fence and _LIST_ITEM.match(lines[-1]):
        best = offset
    return best


@dataclass
class ParagraphBuffer:
    """Paragraph-mode delta batching for one stream of one message."""

    last_flush: float
    pending: str = ""

    def add(self, fragment: str) -> None:
        self.pending += fragment

    def take(self, now: float, *, final: bool = False) -> str | None:
        """The next fragment to emit, or ``None`` while it should keep buffering."""
        if not self.pending:
            return None
        if final:
            return self._cut(len(self.pending), now)
        if len(self.pending) >= HARD_FLUSH_CHARS:
            return self._cut(paragraph_boundary(self.pending) or len(self.pending), now)
        if now - self.last_flush < PARAGRAPH_FLUSH_SECONDS:
            return None
        boundary = paragraph_boundary(self.pending)
        if not boundary:
            return None
        return self._cut(boundary, now)

    def _cut(self, index: int, now: float) -> str:
        fragment, self.pending = self.pending[:index], self.pending[index:]
        self.last_flush = now
        return fragment


class StreamCapture(AsyncCallbackHandler):
    """The streaming deltas of one model call, flushed as fragments of one message.

    Chunk ids are deliberately ignored: a provider need not name its message
    the same way in every chunk (the OpenAI Responses stream alternates between
    the real ``resp_...`` id and LangChain's ``lc_run--<run_id>`` placeholder),
    so keying fragments on the chunk would split one reply across several
    transcript messages. The writer names the message once per model call, and
    its ``message.completed`` reuses that id.
    """

    run_inline = True

    def __init__(
        self, message_id: str, namespace: Sequence[str], emit: Callable[[Fragment], None]
    ) -> None:
        now = time.monotonic()
        self.message_id = message_id
        self.namespace = list(namespace)
        self.streamed = False
        self._emit = emit
        self._text = ParagraphBuffer(last_flush=now)
        self._reasoning = ParagraphBuffer(last_flush=now)
        self._protocol: Literal["v1", "v2"] | None = None

    @contextmanager
    def attached(self) -> Iterator[None]:
        """Receive the deltas of the model calls made inside the block."""
        manager = _attach(self)
        try:
            yield
        finally:
            _detach(manager, self)

    def finish(self) -> None:
        """Flush whatever streamed since the last fragment."""
        try:
            self._flush(final=True)
        except Exception:
            logger.warning(
                "Transcript delta flush failed",
                exc_info=True,
                extra={"transcript_message_id": self.message_id},
            )

    async def on_llm_new_token(
        self,
        token: object,
        *,
        chunk: object = None,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: object,
    ) -> None:
        if self._skip("v1", tags):
            return
        message = getattr(chunk, "message", chunk)
        if not isinstance(message, BaseMessage):
            self._feed(token if isinstance(token, str) else "", "")
            return
        text = message_text(message) or (token if isinstance(token, str) else "")
        self._feed(text, reasoning_text(message))

    async def on_stream_event(
        self,
        event: Mapping[str, object],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: object,
    ) -> None:
        if self._skip("v2", tags) or event.get("event") != "content-block-delta":
            return
        delta = event.get("delta")
        if not isinstance(delta, Mapping):
            return
        text = delta.get("text")
        reasoning = delta.get("reasoning")
        self._feed(
            text if isinstance(text, str) else "",
            reasoning if isinstance(reasoning, str) else "",
        )

    def _skip(self, protocol: Literal["v1", "v2"], tags: list[str] | None) -> bool:
        """Drop hidden model calls, and whichever streaming protocol did not win."""
        if _HIDDEN_TAGS & set(tags or ()):
            return True
        if self._protocol is None:
            self._protocol = protocol
        return self._protocol != protocol

    def _feed(self, text: str, reasoning: str) -> None:
        if not text and not reasoning:
            return
        self.streamed = True
        try:
            if text:
                self._text.add(text)
            if reasoning:
                self._reasoning.add(reasoning)
            self._flush(final=False)
        except Exception:
            logger.warning(
                "Transcript delta capture failed",
                exc_info=True,
                extra={"transcript_message_id": self.message_id},
            )

    def _flush(self, *, final: bool) -> None:
        now = time.monotonic()
        text = self._text.take(now, final=final)
        reasoning = self._reasoning.take(now, final=final)
        if text is None and reasoning is None:
            return
        self._emit(
            Fragment(
                fragment_id=uuid4(),
                message_id=self.message_id,
                namespace=self.namespace,
                text=text,
                reasoning=reasoning,
            )
        )


def _attach(handler: AsyncCallbackHandler) -> CallbackManager | AsyncCallbackManager | None:
    """Add ``handler`` to the node's callback manager so it sees model deltas.

    The model node invokes with no explicit config, so the model inherits the
    contextvar ``RunnableConfig``; its ``callbacks`` manager is where a handler
    has to live to receive the child LLM run's token callbacks.
    """
    try:
        callbacks = get_config().get("callbacks")
    except RuntimeError:
        return None
    if not isinstance(callbacks, (CallbackManager, AsyncCallbackManager)):
        logger.debug("No callback manager on the run config; delta capture disabled")
        return None
    try:
        callbacks.add_handler(handler, inherit=True)
    except Exception:
        logger.warning("Could not attach transcript delta handler", exc_info=True)
        return None
    return callbacks


def _detach(
    manager: CallbackManager | AsyncCallbackManager | None, handler: AsyncCallbackHandler
) -> None:
    if manager is None:
        return
    try:
        manager.remove_handler(handler)
    except Exception:
        logger.warning("Could not detach transcript delta handler", exc_info=True)
