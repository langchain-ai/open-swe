"""The subset of the OpenAI Responses wire format this endpoint speaks."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agent.dashboard.options import DEFAULT_MODEL_EFFORT, normalize_model_choice

DEFAULT_MODEL = "open-swe"
SERVER_LABEL = "open-swe"
TOOL_NAME_PREFIX = "oswe_"

type Role = Literal["user", "assistant", "system", "developer"]
type ResponseStatus = Literal[
    "queued", "in_progress", "completed", "failed", "incomplete", "cancelled"
]
type ItemStatus = Literal["in_progress", "completed", "incomplete"]
type McpCallStatus = Literal["in_progress", "completed", "failed"]
type StreamEventType = Literal[
    "response.created",
    "response.in_progress",
    "response.completed",
    "response.failed",
    "response.incomplete",
    "response.output_item.added",
    "response.output_item.done",
    "response.content_part.added",
    "response.content_part.done",
    "response.output_text.delta",
    "response.output_text.done",
    "response.mcp_call.in_progress",
    "response.mcp_call.completed",
    "response.mcp_call.failed",
]


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class InputContentPart(_Lenient):
    type: str
    text: str | None = None


class InputItem(_Lenient):
    type: str = "message"
    id: str | None = None
    role: Role | None = None
    content: str | list[InputContentPart] | None = None

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "\n".join(part.text for part in self.content or () if part.text)

    @classmethod
    def render(cls, items: list[InputItem]) -> str:
        """The conversation text for the agent; system and developer prompts target the client's own harness."""
        messages = [
            (item.role, text)
            for item in items
            if item.type == "message"
            and item.role in {"user", "assistant"}
            and (text := item.text().strip())
        ]
        if all(role == "user" for role, _ in messages):
            return "\n\n".join(text for _, text in messages)
        return "\n\n".join(f"**{role}**: {text}" for role, text in messages)


class ConversationRef(BaseModel):
    id: str


class ReasoningParam(_Lenient):
    effort: str | None = None


class CreateResponseRequest(_Lenient):
    model: str = DEFAULT_MODEL
    input: str | list[InputItem]
    stream: bool = False
    background: bool = False
    previous_response_id: str | None = None
    conversation: str | ConversationRef | None = None
    reasoning: ReasoningParam | None = None
    metadata: dict[str, str] | None = None

    def items(self) -> list[InputItem]:
        if isinstance(self.input, str):
            return [InputItem(role="user", content=self.input)]
        return self.input

    def conversation_id(self) -> str | None:
        if isinstance(self.conversation, ConversationRef):
            return self.conversation.id
        return self.conversation

    def agent_model(self) -> tuple[str, str] | None:
        """``model`` as an Open SWE model id; anything else runs the workspace default."""
        requested = self.reasoning.effort if self.reasoning else None
        for effort in (requested, DEFAULT_MODEL_EFFORT):
            model_id, chosen = normalize_model_choice(self.model, effort)
            if model_id and chosen:
                return model_id, chosen
        return None


class OutputText(BaseModel):
    type: Literal["output_text"] = "output_text"
    text: str = ""
    annotations: list[JsonValue] = Field(default_factory=list)


class MessageItem(BaseModel):
    type: Literal["message"] = "message"
    id: str
    status: ItemStatus = "in_progress"
    role: Literal["assistant"] = "assistant"
    content: list[OutputText] = Field(default_factory=list)


class McpCallItem(BaseModel):
    type: Literal["mcp_call"] = "mcp_call"
    id: str
    server_label: str = SERVER_LABEL
    name: str
    arguments: str
    status: McpCallStatus = "in_progress"
    output: str | None = None
    error: str | None = None


type OutputItem = Annotated[MessageItem | McpCallItem, Field(discriminator="type")]


class InputTokensDetails(BaseModel):
    cached_tokens: int = 0


class OutputTokensDetails(BaseModel):
    reasoning_tokens: int = 0


class Usage(BaseModel):
    input_tokens: int = 0
    input_tokens_details: InputTokensDetails = Field(default_factory=InputTokensDetails)
    output_tokens: int = 0
    output_tokens_details: OutputTokensDetails = Field(default_factory=OutputTokensDetails)
    total_tokens: int = 0


class ResponseError(BaseModel):
    code: str
    message: str


class IncompleteDetails(BaseModel):
    reason: str


class Response(BaseModel):
    id: str
    object: Literal["response"] = "response"
    created_at: int
    status: ResponseStatus
    model: str
    output: list[OutputItem] = Field(default_factory=list)
    usage: Usage | None = None
    error: ResponseError | None = None
    incomplete_details: IncompleteDetails | None = None
    conversation: ConversationRef
    previous_response_id: str | None = None
    background: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)
    parallel_tool_calls: bool = False
    tool_choice: Literal["auto"] = "auto"
    tools: list[JsonValue] = Field(default_factory=list)
    store: bool = True


class StreamEvent(BaseModel):
    type: StreamEventType
    sequence_number: int
    response: Response | None = None
    output_index: int | None = None
    item: OutputItem | None = None
    item_id: str | None = None
    content_index: int | None = None
    part: OutputText | None = None
    delta: str | None = None
    text: str | None = None

    def sse(self) -> str:
        return f"event: {self.type}\ndata: {self.model_dump_json(exclude_none=True)}\n\n"


class ModelObject(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str = SERVER_LABEL


class ModelList(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelObject]
