"""Project one run's transcript events onto a Responses object and its stream events."""

import json
from collections.abc import Mapping
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from openswe.openai_responses.client_tools import ClientToolKind
from openswe.openai_responses.ids import OpenSweId
from openswe.openai_responses.models import (
    TOOL_NAME_PREFIX,
    CustomToolCallItem,
    FunctionCallItem,
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
from openswe.transcript import tool_output
from openswe.transcript.events import (
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
        self,
        response: Response,
        ids: OpenSweId,
        *,
        web_search_tools: bool = False,
        client_tools: Mapping[str, ClientToolKind] | None = None,
    ) -> None:
        self.response = response
        self._ids = ids
        self._web_search_tools = web_search_tools
        self._client_tools = dict(client_tools or {})
        self._client_calls: set[str] = set()
        self._turn_id: UUID | None = None
        self._sequence = 0
        self._messages: dict[str, tuple[int, MessageItem]] = {}
        self._open: set[str] = set()
        self._tools: dict[str, tuple[int, McpCallItem]] = {}
        self._searches: dict[str, tuple[int, WebSearchCallItem]] = {}
        self._usage: Usage | None = None

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
            self._add_usage(body.usage)
        if body.message_id not in self._messages and not body.text:
            return []
        events = self._open_message(body.message_id)
        _, item = self._messages[body.message_id]
        # The canonical text is stripped; the streamed fragments are not.
        streamed = item.content[0].text.lstrip()
        if body.text.startswith(streamed) and len(body.text) > len(streamed):
            events.append(self._text_delta(body.message_id, body.text[len(streamed) :]))
        item.content[0].text = body.text
        return [*events, *self._close_message(body.message_id, "completed")]

    def _add_usage(self, usage: MessageUsage) -> None:
        """Each AI message reports its own model call; a response spans all of them."""
        total = self._usage or Usage()
        input_tokens = usage.input_tokens or 0
        output_tokens = usage.output_tokens or 0
        total.input_tokens += input_tokens
        total.output_tokens += output_tokens
        total.total_tokens += usage.total_tokens or input_tokens + output_tokens
        self._usage = total

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

    def _client_call(self, body: ToolStarted, kind: ClientToolKind) -> list[StreamEvent]:
        self._client_calls.add(body.tool_call_id)
        index = len(self.response.output)
        item: FunctionCallItem | CustomToolCallItem
        if kind == "custom":
            raw = body.input.get("input")
            item = CustomToolCallItem(
                id=self._ids.item_id("ctc", body.tool_call_id),
                call_id=body.tool_call_id,
                name=body.name,
                input=raw if isinstance(raw, str) else json.dumps(body.input),
            )
        else:
            item = FunctionCallItem(
                id=self._ids.item_id("fc", body.tool_call_id),
                call_id=body.tool_call_id,
                name=body.name,
                arguments=json.dumps(body.input),
            )
        self.response.output.append(item)
        added = self._event("response.output_item.added", output_index=index, item=item)
        if isinstance(item, CustomToolCallItem):
            done = self._event(
                "response.custom_tool_call_input.done",
                item_id=item.id,
                output_index=index,
                input=item.input,
            )
        else:
            done = self._event(
                "response.function_call_arguments.done",
                item_id=item.id,
                output_index=index,
                arguments=item.arguments,
            )
        return [
            added,
            done,
            self._event("response.output_item.done", output_index=index, item=item),
        ]

    def _start_tool(self, body: ToolStarted) -> list[StreamEvent]:
        if body.tool_call_id in self._tools or body.tool_call_id in self._searches:
            return []
        if body.tool_call_id in self._client_calls:
            return []
        if (kind := self._client_tools.get(body.name)) is not None:
            return self._client_call(body, kind)
        index = len(self.response.output)
        if self._web_search_tools:
            search = WebSearchCallItem.for_call(
                self._ids.item_id("ws", body.tool_call_id), body.name, body.input
            )
            self.response.output.append(search)
            self._searches[body.tool_call_id] = (index, search)
            return [
                self._event("response.output_item.added", output_index=index, item=search),
                self._event(
                    "response.web_search_call.in_progress", item_id=search.id, output_index=index
                ),
            ]
        item = McpCallItem(
            id=self._ids.item_id("mcp", body.tool_call_id),
            name=TOOL_NAME_PREFIX + body.name,
            arguments=json.dumps(body.input),
        )
        self.response.output.append(item)
        self._tools[body.tool_call_id] = (index, item)
        return [
            self._event("response.output_item.added", output_index=index, item=item),
            self._event("response.mcp_call.in_progress", item_id=item.id, output_index=index),
        ]

    async def _complete_tool(self, thread_id: str, body: ToolCompleted) -> list[StreamEvent]:
        if body.tool_call_id in self._client_calls:
            return []
        failed = body.status == "error"
        if body.tool_call_id in self._searches:
            index, search = self._searches[body.tool_call_id]
            search.status = "failed" if failed else "completed"
            return [
                self._event(
                    "response.web_search_call.completed", item_id=search.id, output_index=index
                ),
                self._event("response.output_item.done", output_index=index, item=search),
            ]
        if body.tool_call_id not in self._tools:
            return []
        index, item = self._tools[body.tool_call_id]
        output = body.output_preview
        if body.has_output:
            output = await tool_output.load(thread_id, body.tool_call_id) or output
        item.status = "failed" if failed else "completed"
        item.output = output
        item.error = (output or "tool call failed") if failed else None
        return [
            self._event(
                "response.mcp_call.failed" if failed else "response.mcp_call.completed",
                item_id=item.id,
                output_index=index,
            ),
            self._event("response.output_item.done", output_index=index, item=item),
        ]

    def _finish(self, status: Terminal) -> list[StreamEvent]:
        events = [
            event
            for message_id in list(self._open)
            for event in self._close_message(message_id, "incomplete")
        ]
        self.response.usage = self._usage
        self.response.status = status
        return [*events, self._event(_TERMINAL_EVENTS[status], response=self.response)]
