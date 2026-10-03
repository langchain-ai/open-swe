"""Translate a thread's LangGraph v3 event stream into AG-UI events."""

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from ag_ui.core import (
    BaseEvent,
    ReasoningEndEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    ReasoningStartEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from pydantic import ValidationError

from agent.agui.langgraph_events import (
    STREAM_EVENT,
    TRANSLATED_METHODS,
    BlockDelta,
    ContentBlock,
    ContentBlockDelta,
    ContentBlockFinish,
    ContentBlockStart,
    MessageFinish,
    MessagesData,
    MessageStart,
    ReasoningDelta,
    TextDelta,
    ToolError,
    ToolFinished,
    ToolsData,
    ToolsEvent,
)
from agent.agui.messages import reasoning_message_id, tool_output_text

logger = logging.getLogger(__name__)


@dataclass
class _Block:
    kind: Literal["text", "reasoning", "tool"]
    id: str | None = None
    name: str | None = None
    started: bool = False
    args: str = ""


@dataclass
class _Message:
    id: str
    blocks: dict[int, _Block] = field(default_factory=dict)
    started: bool = False


class AgUiTranslator:
    """Stateful mapping from root-namespace message and tool events to AG-UI events.

    Messages the client already holds from a snapshot are skipped, so a replayed
    event cannot reopen them.
    """

    def __init__(self, known_message_ids: set[str] | None = None) -> None:
        self._known = set(known_message_ids or ())
        self._message: _Message | None = None
        self._open_tool_calls: set[str] = set()

    def translate(self, raw: Mapping[str, Any]) -> list[BaseEvent]:
        if raw.get("method") not in TRANSLATED_METHODS:
            return []
        try:
            event = STREAM_EVENT.validate_python(raw)
        except ValidationError:
            logger.warning(
                "Skipping unparseable LangGraph stream event",
                extra={"stream_method": raw.get("method")},
                exc_info=True,
            )
            return []
        if event.params.namespace:
            return []
        if isinstance(event, ToolsEvent):
            return self._tool(event.params.data)
        return self._messages(event.params.data)

    def close(self) -> list[BaseEvent]:
        """End everything still open, as a run that stops mid-message requires."""
        events: list[BaseEvent] = []
        if self._message is not None:
            events.extend(self._end_message(self._message))
        for tool_call_id in sorted(self._open_tool_calls):
            events.append(ToolCallEndEvent(tool_call_id=tool_call_id))
        self._open_tool_calls.clear()
        return events

    def _messages(self, data: MessagesData) -> list[BaseEvent]:
        if isinstance(data, MessageStart):
            events = self.close() if self._message is not None else []
            if data.role == "ai" and data.id not in self._known:
                self._known.add(data.id)
                self._message = _Message(data.id)
            return events
        message = self._message
        if message is None:
            return []
        if isinstance(data, ContentBlockStart):
            return self._start_block(message, data.index, data.content)
        if isinstance(data, ContentBlockDelta):
            return self._delta(message, data)
        if isinstance(data, ContentBlockFinish):
            return self._finish_block(data.index, data.content)
        if isinstance(data, MessageFinish):
            return self._end_message(message)
        return []

    def _opened(self, message: _Message) -> list[BaseEvent]:
        """Open the assistant message on its first text or tool call, after any reasoning."""
        if message.started:
            return []
        message.started = True
        return [TextMessageStartEvent(message_id=message.id, role="assistant")]

    def _end_message(self, message: _Message) -> list[BaseEvent]:
        events = [
            event for index in list(message.blocks) for event in self._finish_block(index, None)
        ]
        if message.started:
            events.append(TextMessageEndEvent(message_id=message.id))
        self._message = None
        return events

    def _start_block(self, message: _Message, index: int, content: ContentBlock) -> list[BaseEvent]:
        if content.type == "text":
            message.blocks[index] = _Block("text")
            return self._text(message, content.text)
        if content.type == "reasoning":
            # Opened even when empty: providers that only summarize reasoning at the end
            # fill this same message from the final snapshot, keeping it in place.
            reasoning_id = reasoning_message_id(message.id)
            block = _Block("reasoning", id=reasoning_id, started=True)
            message.blocks[index] = block
            return [
                ReasoningStartEvent(message_id=reasoning_id),
                ReasoningMessageStartEvent(message_id=reasoning_id),
                *self._reasoning(block, content.reasoning),
            ]
        if content.type in {"tool_call_chunk", "tool_call"}:
            block = _Block("tool")
            message.blocks[index] = block
            return self._tool_chunk(message, block, content)
        return []

    def _delta(self, message: _Message, data: ContentBlockDelta) -> list[BaseEvent]:
        block = message.blocks.get(data.index)
        delta = data.delta
        if block is None:
            return []
        if isinstance(delta, TextDelta) and block.kind == "text":
            return self._text(message, delta.text)
        if isinstance(delta, ReasoningDelta) and block.kind == "reasoning":
            return self._reasoning(block, delta.reasoning)
        if isinstance(delta, BlockDelta) and block.kind == "tool":
            return self._tool_chunk(message, block, delta.fields)
        return []

    def _finish_block(self, index: int, content: ContentBlock | None) -> list[BaseEvent]:
        message = self._message
        block = message.blocks.pop(index, None) if message else None
        if message is None or block is None:
            return []
        if block.kind == "reasoning" and block.started and block.id:
            return [
                ReasoningMessageEndEvent(message_id=block.id),
                ReasoningEndEvent(message_id=block.id),
            ]
        if block.kind != "tool":
            return []
        events: list[BaseEvent] = []
        if content is not None and not block.started:
            events.extend(self._tool_chunk(message, block, content, final=True))
        if block.started and block.id:
            self._open_tool_calls.discard(block.id)
            events.append(ToolCallEndEvent(tool_call_id=block.id))
        return events

    def _reasoning(self, block: _Block, text: str | None) -> list[BaseEvent]:
        if not text or block.id is None:
            return []
        return [ReasoningMessageContentEvent(message_id=block.id, delta=text)]

    def _text(self, message: _Message, text: str | None) -> list[BaseEvent]:
        if not text:
            return []
        return [*self._opened(message), TextMessageContentEvent(message_id=message.id, delta=text)]

    def _tool_chunk(
        self,
        message: _Message,
        block: _Block,
        content: ContentBlock,
        *,
        final: bool = False,
    ) -> list[BaseEvent]:
        block.id = block.id or content.id
        block.name = block.name or content.name
        args = content.args
        if isinstance(args, dict):
            delta = json.dumps(args)
            block.args = delta
        else:
            # Chunks arrive either as increments or as the whole string so far.
            chunk = args or ""
            delta = chunk.removeprefix(block.args) if chunk.startswith(block.args) else chunk
            block.args += delta
        if not block.started:
            if not block.id or not block.name:
                return []
            block.started = True
            self._open_tool_calls.add(block.id)
            events: list[BaseEvent] = [
                *self._opened(message),
                ToolCallStartEvent(
                    tool_call_id=block.id,
                    tool_call_name=block.name,
                    parent_message_id=message.id,
                ),
            ]
            if block.args:
                events.append(ToolCallArgsEvent(tool_call_id=block.id, delta=block.args))
            return events
        if final or not delta or block.id is None:
            return []
        return [ToolCallArgsEvent(tool_call_id=block.id, delta=delta)]

    def _tool(self, data: ToolsData) -> list[BaseEvent]:
        if isinstance(data, ToolFinished):
            content = tool_output_text(data.output)
        elif isinstance(data, ToolError):
            content = data.message
        else:
            return []
        events: list[BaseEvent] = []
        if data.tool_call_id in self._open_tool_calls:
            self._open_tool_calls.discard(data.tool_call_id)
            events.append(ToolCallEndEvent(tool_call_id=data.tool_call_id))
        events.append(
            ToolCallResultEvent(
                message_id=f"{data.tool_call_id}:result",
                tool_call_id=data.tool_call_id,
                content=content,
                role="tool",
            )
        )
        return events
