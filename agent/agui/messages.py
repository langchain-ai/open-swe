"""Convert LangChain message state into AG-UI messages and back."""

import json
import logging
from typing import Literal

from ag_ui.core import (
    AssistantMessage,
    DataSource,
    FunctionCall,
    ImagePart,
    Message,
    ReasoningMessage,
    TextPart,
    ToolCall,
    ToolMessage,
    UrlSource,
    UserMessage,
)
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

logger = logging.getLogger(__name__)


class _LangChainBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str = ""
    text: str | None = None
    reasoning: str | None = None
    thinking: str | None = None
    base64: str | None = None
    url: str | None = None
    mime_type: str | None = None


class _LangChainToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    name: str
    args: dict[str, JsonValue] = {}


class LangChainMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    id: str | None = None
    content: str | list[_LangChainBlock | str] = ""
    tool_calls: list[_LangChainToolCall] = []
    tool_call_id: str | None = None
    status: str | None = None
    artifact: JsonValue = None

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "".join(
            block if isinstance(block, str) else block.text or ""
            for block in self.content
            if isinstance(block, str) or block.type == "text"
        )

    def reasoning(self) -> str:
        if isinstance(self.content, str):
            return ""
        return "".join(
            block.reasoning or block.thinking or ""
            for block in self.content
            if not isinstance(block, str) and block.type in {"reasoning", "thinking"}
        )


_MESSAGES: TypeAdapter[list[LangChainMessage]] = TypeAdapter(list[LangChainMessage])


class _StateValues(BaseModel):
    model_config = ConfigDict(extra="ignore")

    messages: list[JsonValue] = []


def reasoning_message_id(message_id: str) -> str:
    """One reasoning message per assistant message, shared by the stream and snapshots."""
    return f"{message_id}:reasoning"


def tool_output_text(output: JsonValue) -> str:
    """The text a tool returned, whether LangGraph sent a ToolMessage or a bare value."""
    if isinstance(output, str):
        return output
    try:
        return LangChainMessage.model_validate(output).text()
    except ValidationError:
        return json.dumps(output)


def _user_content(message: LangChainMessage) -> str | list[TextPart | ImagePart]:
    if isinstance(message.content, str):
        return message.content
    parts: list[TextPart | ImagePart] = []
    for block in message.content:
        if isinstance(block, str):
            parts.append(TextPart(text=block))
        elif block.type == "text" and block.text:
            parts.append(TextPart(text=block.text))
        elif block.type == "image" and block.base64 and block.mime_type:
            parts.append(
                ImagePart(source=DataSource(value=block.base64, mime_type=block.mime_type))
            )
        elif block.type == "image" and block.url:
            parts.append(ImagePart(source=UrlSource(value=block.url, mime_type=block.mime_type)))
    return parts


def _converted(message: LangChainMessage, message_id: str) -> list[Message]:
    match message.type:
        case "human":
            return [UserMessage(id=message_id, content=_user_content(message))]
        case "ai":
            converted: list[Message] = []
            if reasoning := message.reasoning():
                converted.append(
                    ReasoningMessage(id=reasoning_message_id(message_id), content=reasoning)
                )
            calls = [
                ToolCall(
                    id=call.id,
                    function=FunctionCall(name=call.name, arguments=json.dumps(call.args)),
                )
                for call in message.tool_calls
                if call.id
            ]
            converted.append(
                AssistantMessage(
                    id=message_id,
                    content=message.text() or None,
                    tool_calls=calls or None,
                )
            )
            return converted
        case "tool" if message.tool_call_id:
            return [
                ToolMessage(
                    id=message_id,
                    tool_call_id=message.tool_call_id,
                    content=message.text(),
                    error=message.text() if message.status == "error" else None,
                    metadata={"artifact": message.artifact} if message.artifact else None,
                )
            ]
        case _:
            return []


def agui_messages(state_values: JsonValue) -> list[Message]:
    """The thread's conversation as AG-UI messages, in order."""
    raw = _StateValues.model_validate(state_values).messages
    try:
        messages = _MESSAGES.validate_python(raw)
    except ValidationError:
        logger.warning("Thread state messages are not LangChain messages", exc_info=True)
        return []
    converted: list[Message] = []
    for index, message in enumerate(messages):
        converted.extend(_converted(message, message.id or f"state-{index}"))
    return converted


class _HumanText(BaseModel):
    type: Literal["text"] = "text"
    text: str


class _HumanImage(BaseModel):
    type: Literal["image"] = "image"
    base64: str
    mime_type: str


class HumanTurn(BaseModel):
    """The human message a run starts with, keyed by the id the client already shows."""

    type: Literal["human"] = "human"
    id: str
    content: list[_HumanText | _HumanImage]


def human_turn(message: UserMessage) -> HumanTurn:
    if isinstance(message.content, str):
        return HumanTurn(id=message.id, content=[_HumanText(text=message.content)])
    blocks: list[_HumanText | _HumanImage] = []
    for part in message.content:
        if isinstance(part, TextPart) and part.text.strip():
            blocks.append(_HumanText(text=part.text))
        elif isinstance(part, ImagePart) and isinstance(part.source, DataSource):
            blocks.append(_HumanImage(base64=part.source.value, mime_type=part.source.mime_type))
    return HumanTurn(id=message.id, content=blocks)
