"""Project one run's transcript events onto a Responses object and its stream events."""

import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from agent.openai_responses.ids import OpenSweId
from agent.openai_responses.models import (
    TOOL_NAME_PREFIX,
    IncompleteDetails,
    ItemStatus,
    McpCallItem,
    MessageItem,
    OutputText,
    Response,
    ResponseError,
    StreamEvent,
    StreamEventType,
    Usage,
    WebSearchCallItem,
)
from agent.transcript import tool_output
from agent.transcript.events import (
    TRANSCRIPT_EVENT_ADAPTER,
    MessageAppended,
    MessageCompleted,
    MessageUsage,
    StoredEvent,
    ToolCompleted,
    ToolStarted,
    TranscriptEvent,
    TurnCompleted,
    TurnFailed,
    TurnInterrupted,
    TurnQueued,
    TurnStarted,
)

type Terminal = Literal["completed", "failed", "incomplete"]

_TERMINAL_EVENTS: dict[Terminal, StreamEventType] = {
    "completed": "response.completed",
    "failed": "response.failed",
    "incomplete": "response.incomplete",
}


class ResponseProjection:
    """Folds a run's root-namespace transcript into Responses output items.

    The run is identified by the turn whose ``turn.started`` or ``turn.queued``
    names its run id; events of other turns and of subagents are skipped.
    """

    def __init__(
        self, response: Response, ids: OpenSweId, *, mirror_web_search: bool = False
    ) -> None:
        self.response = response
        self._ids = ids
        self._mirror_web_search = mirror_web_search
        self._turn_id: UUID | None = None
        self._sequence = 0
        self._messages: dict[str, tuple[int, MessageItem]] = {}
        self._open: set[str] = set()
        self._tools: dict[str, tuple[int, McpCallItem]] = {}
        self._searches: dict[str, tuple[int, WebSearchCallItem]] = {}
        self._usage: MessageUsage | None = None

    @property
    def done(self) -> bool:
        return self.response.status in {"completed", "failed", "incomplete", "cancelled"}

    @property
    def started(self) -> bool:
        return self._turn_id is not None

    def abandon(self, message: str) -> list[StreamEvent]:
        self.response.error = ResponseError(code="server_error", message=message)
        return self._finish("failed")

    def start(self) -> list[StreamEvent]:
        self.response.status = "in_progress"
        return [
            self._event("response.created", response=self._summary()),
            self._event("response.in_progress", response=self._summary()),
        ]

    def heartbeat(self) -> StreamEvent:
        return self._event("response.in_progress", response=self._summary())

    async def apply(self, stored: StoredEvent) -> list[StreamEvent]:
        body = TRANSCRIPT_EVENT_ADAPTER.validate_python(stored.payload)
        if self._turn_id is None:
            if isinstance(body, TurnStarted | TurnQueued) and body.run_id == self._ids.run_id:
                self._turn_id = body.turn_id
            return []
        if not self._in_turn(body):
            return []
        match body:
            case MessageAppended(text=str(text)) if text:
                return [
                    *self._open_message(body.message_id),
                    self._text_delta(body.message_id, text),
                ]
            case MessageCompleted(role="ai"):
                return self._complete_message(body)
            case ToolStarted():
                return self._start_tool(body)
            case ToolCompleted():
                return await self._complete_tool(stored.thread_id, body)
            case TurnCompleted():
                return self._finish("completed")
            case TurnFailed():
                self.response.error = ResponseError(code="server_error", message=body.error)
                return self._finish("failed")
            case TurnInterrupted():
                self.response.incomplete_details = IncompleteDetails(reason="interrupted")
                return self._finish("incomplete")
            case _:
                return []

    def _in_turn(self, body: TranscriptEvent) -> bool:
        match body:
            case MessageAppended() | MessageCompleted() | ToolStarted() | ToolCompleted():
                return body.turn_id == self._turn_id and not body.namespace
            case TurnCompleted() | TurnFailed() | TurnInterrupted():
                return body.turn_id == self._turn_id
            case _:
                return False

    def _event(self, type: StreamEventType, **fields: object) -> StreamEvent:
        # Items keep changing after an event names them; each event carries the state it saw.
        frozen = {
            key: value.model_copy(deep=True) if isinstance(value, BaseModel) else value
            for key, value in fields.items()
        }
        event = StreamEvent.model_validate(
            {"type": type, "sequence_number": self._sequence, **frozen}
        )
        self._sequence += 1
        return event

    def _summary(self) -> Response:
        return self.response.model_copy(update={"output": []})

    def _open_message(self, message_id: str) -> list[StreamEvent]:
        if message_id in self._messages:
            return []
        index = len(self.response.output)
        item = MessageItem(id=self._ids.item_id("msg", message_id))
        self.response.output.append(item)
        self._messages[message_id] = (index, item)
        self._open.add(message_id)
        added = self._event("response.output_item.added", output_index=index, item=item)
        item.content.append(OutputText())
        return [
            added,
            self._event(
                "response.content_part.added",
                item_id=item.id,
                output_index=index,
                content_index=0,
                part=OutputText(),
            ),
        ]

    def _text_delta(self, message_id: str, text: str) -> StreamEvent:
        index, item = self._messages[message_id]
        item.content[0].text += text
        return self._event(
            "response.output_text.delta",
            item_id=item.id,
            output_index=index,
            content_index=0,
            delta=text,
        )

    def _complete_message(self, body: MessageCompleted) -> list[StreamEvent]:
        if body.usage is not None:
            self._usage = body.usage
        if body.message_id not in self._messages and not body.text:
            return []
        events = self._open_message(body.message_id)
        _, item = self._messages[body.message_id]
        streamed = item.content[0].text
        if body.text.startswith(streamed) and len(body.text) > len(streamed):
            events.append(self._text_delta(body.message_id, body.text[len(streamed) :]))
        item.content[0].text = body.text
        return [*events, *self._close_message(body.message_id, "completed")]

    def _close_message(self, message_id: str, status: ItemStatus) -> list[StreamEvent]:
        if message_id not in self._open:
            return []
        self._open.discard(message_id)
        index, item = self._messages[message_id]
        item.status = status
        part = item.content[0]
        return [
            self._event(
                "response.output_text.done",
                item_id=item.id,
                output_index=index,
                content_index=0,
                text=part.text,
            ),
            self._event(
                "response.content_part.done",
                item_id=item.id,
                output_index=index,
                content_index=0,
                part=part,
            ),
            self._event("response.output_item.done", output_index=index, item=item),
        ]

    def _start_tool(self, body: ToolStarted) -> list[StreamEvent]:
        if body.tool_call_id in self._tools:
            return []
        index = len(self.response.output)
        item = McpCallItem(
            id=self._ids.item_id("mcp", body.tool_call_id),
            name=TOOL_NAME_PREFIX + body.name,
            arguments=json.dumps(body.input),
        )
        self.response.output.append(item)
        self._tools[body.tool_call_id] = (index, item)
        events = [
            self._event("response.output_item.added", output_index=index, item=item),
            self._event("response.mcp_call.in_progress", item_id=item.id, output_index=index),
        ]
        if self._mirror_web_search:
            search = WebSearchCallItem.for_call(
                self._ids.item_id("ws", body.tool_call_id), body.name, body.input
            )
            self.response.output.append(search)
            self._searches[body.tool_call_id] = (index + 1, search)
            events += [
                self._event("response.output_item.added", output_index=index + 1, item=search),
                self._event(
                    "response.web_search_call.in_progress",
                    item_id=search.id,
                    output_index=index + 1,
                ),
            ]
        return events

    async def _complete_tool(self, thread_id: str, body: ToolCompleted) -> list[StreamEvent]:
        if body.tool_call_id not in self._tools:
            return []
        index, item = self._tools[body.tool_call_id]
        output = body.output_preview
        if body.has_output:
            output = await tool_output.load(thread_id, body.tool_call_id) or output
        failed = body.status == "error"
        item.status = "failed" if failed else "completed"
        item.output = output
        item.error = (output or "tool call failed") if failed else None
        events = [
            self._event(
                "response.mcp_call.failed" if failed else "response.mcp_call.completed",
                item_id=item.id,
                output_index=index,
            ),
            self._event("response.output_item.done", output_index=index, item=item),
        ]
        if body.tool_call_id in self._searches:
            search_index, search = self._searches[body.tool_call_id]
            search.status = item.status
            events += [
                self._event(
                    "response.web_search_call.completed",
                    item_id=search.id,
                    output_index=search_index,
                ),
                self._event("response.output_item.done", output_index=search_index, item=search),
            ]
        return events

    def _finish(self, status: Terminal) -> list[StreamEvent]:
        events = [
            event
            for message_id in list(self._open)
            for event in self._close_message(message_id, "incomplete")
        ]
        if self._usage is not None:
            input_tokens = self._usage.input_tokens or 0
            output_tokens = self._usage.output_tokens or 0
            self.response.usage = Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=self._usage.total_tokens or input_tokens + output_tokens,
            )
        self.response.status = status
        return [*events, self._event(_TERMINAL_EVENTS[status], response=self.response)]
