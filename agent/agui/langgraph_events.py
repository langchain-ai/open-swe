"""The LangGraph v3 stream events the AG-UI translator consumes."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter


class _Event(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ContentBlock(_Event):
    type: str
    text: str | None = None
    reasoning: str | None = None
    id: str | None = None
    name: str | None = None
    args: str | dict[str, JsonValue] | None = None


class TextDelta(_Event):
    type: Literal["text-delta"]
    text: str


class ReasoningDelta(_Event):
    type: Literal["reasoning-delta"]
    reasoning: str


class BlockDelta(_Event):
    type: Literal["block-delta"]
    fields: ContentBlock


class DataDelta(_Event):
    type: Literal["data-delta"]


Delta = Annotated[TextDelta | ReasoningDelta | BlockDelta | DataDelta, Field(discriminator="type")]


class MessageStart(_Event):
    event: Literal["message-start"]
    role: str
    id: str


class ContentBlockStart(_Event):
    event: Literal["content-block-start"]
    index: int
    content: ContentBlock


class ContentBlockDelta(_Event):
    event: Literal["content-block-delta"]
    index: int
    delta: Delta | None = None


class ContentBlockFinish(_Event):
    event: Literal["content-block-finish"]
    index: int
    content: ContentBlock


class MessageFinish(_Event):
    event: Literal["message-finish"]


class MessageError(_Event):
    event: Literal["error"]
    message: str


MessagesData = Annotated[
    MessageStart
    | ContentBlockStart
    | ContentBlockDelta
    | ContentBlockFinish
    | MessageFinish
    | MessageError,
    Field(discriminator="event"),
]


class ToolFinished(_Event):
    event: Literal["tool-finished"]
    tool_call_id: str
    output: JsonValue = None


class ToolError(_Event):
    event: Literal["tool-error"]
    tool_call_id: str
    message: str


class ToolStarted(_Event):
    event: Literal["tool-started"]
    tool_call_id: str


class ToolOutputDelta(_Event):
    event: Literal["tool-output-delta"]
    tool_call_id: str


ToolsData = Annotated[
    ToolStarted | ToolOutputDelta | ToolFinished | ToolError,
    Field(discriminator="event"),
]


class MessagesParams(_Event):
    namespace: list[str]
    data: MessagesData


class ToolsParams(_Event):
    namespace: list[str]
    data: ToolsData


class MessagesEvent(_Event):
    method: Literal["messages"]
    params: MessagesParams


class ToolsEvent(_Event):
    method: Literal["tools"]
    params: ToolsParams


StreamEvent = Annotated[MessagesEvent | ToolsEvent, Field(discriminator="method")]
STREAM_EVENT: TypeAdapter[MessagesEvent | ToolsEvent] = TypeAdapter(StreamEvent)
TRANSLATED_METHODS = frozenset({"messages", "tools"})
