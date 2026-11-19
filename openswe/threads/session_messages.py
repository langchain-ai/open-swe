"""The messages a local coding-agent session becomes, whichever agent wrote its transcript."""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.messages.tool import ToolCall

logger = logging.getLogger(__name__)

MISSING_TOOL_RESULT = "No result was recorded before the session was uploaded."
MAX_TITLE_CHARS = 80


class TranscriptError(ValueError):
    """The transcript is not a session of the agent it claims to be from."""


@dataclass(frozen=True, kw_only=True)
class LocalSession:
    title: str | None
    messages: list[BaseMessage]
    # The agent's own id for the session.
    session_id: str | None = None

    @property
    def key(self) -> str:
        """The session's id, or a digest of its parsed messages and their ids when it has none."""
        if self.session_id:
            return self.session_id
        dumps = "\n".join(message.model_dump_json() for message in self.messages)
        return hashlib.sha256(dumps.encode()).hexdigest()


class Transcript(Protocol):
    """A session's JSONL taken one line at a time."""

    def add(self, line: str) -> None: ...

    def session(self) -> LocalSession: ...


class SessionMessages:
    """Folds a session into messages, keeping every tool call answered before the next human turn."""

    def __init__(self) -> None:
        self.messages: list[BaseMessage] = []
        self._ai: dict[str, AIMessage] = {}
        self._calls: dict[str, str] = {}
        self._pending: dict[str, None] = {}
        self._deferred: list[HumanMessage] = []

    def human(self, text: str, message_id: str | None) -> None:
        if not text:
            return
        message = HumanMessage(content=text, id=message_id)
        if self._pending:
            self._deferred.append(message)
        else:
            self.messages.append(message)

    def assistant(self, message_id: str | None, text: str, calls: list[ToolCall]) -> None:
        """Add a reply; parts sharing ``message_id`` join the message they belong to."""
        existing = self._ai.get(message_id) if message_id else None
        if existing is None:
            if not text and not calls:
                return
            self._flush_deferred()
            message = AIMessage(content=text, tool_calls=calls, id=message_id)
            if message_id:
                self._ai[message_id] = message
            self.messages.append(message)
        else:
            existing.content = "\n\n".join(part for part in (str(existing.content), text) if part)
            existing.tool_calls.extend(calls)
        for call in calls:
            if call["id"]:
                self._calls[call["id"]] = call["name"]
                self._pending[call["id"]] = None

    def tool_result(self, call_id: str, content: str, *, error: bool = False) -> None:
        name = self._calls.get(call_id)
        if name is None or call_id not in self._pending:
            logger.warning(
                "Dropped a tool result with no open tool call", extra={"tool_call_id": call_id}
            )
            return
        del self._pending[call_id]
        self.messages.append(
            ToolMessage(
                content=content,
                tool_call_id=call_id,
                name=name,
                status="error" if error else "success",
            )
        )
        if not self._pending:
            self._flush_deferred()

    def finish(self) -> list[BaseMessage]:
        self._flush_deferred()
        return self.messages

    def title(self, clean: Callable[[str], str]) -> str | None:
        """The first human prompt as the person typed it, cut to a title."""
        first = next((m for m in self.messages if isinstance(m, HumanMessage)), None)
        if first is None:
            return None
        return clean(str(first.content))[:MAX_TITLE_CHARS] or None

    def _flush_deferred(self) -> None:
        """Close calls that never got a result, then release the humans held behind them."""
        for call_id in list(self._pending):
            self.messages.append(
                ToolMessage(
                    content=MISSING_TOOL_RESULT, tool_call_id=call_id, name=self._calls[call_id]
                )
            )
        self._pending.clear()
        self.messages.extend(self._deferred)
        self._deferred.clear()
