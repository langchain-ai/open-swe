"""Keep non-image bytes out of ``read_file`` image blocks.

``read_file`` picks the content-block type from the file extension, so a
``.png`` holding something else (for example the JSON error body an expired
download link returns) is sent to the model as an image. Providers reject the
request with HTTP 400, and because the tool message is checkpointed, every
later turn on the thread fails the same way. This middleware checks the magic
bytes and downgrades a mismatch to a plain-text tool result.
"""

import base64
import binascii
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentState, ModelRequest, ModelResponse
from langchain_core.messages import BaseMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from agent.dashboard.options import model_image_budget
from agent.middleware.trace import OpenSWEMiddleware

_IMAGE_SIGNATURES: tuple[tuple[bytes, ...], ...] = (
    (b"\x89PNG\r\n\x1a\n",),
    (b"\xff\xd8\xff",),
    (b"GIF87a",),
    (b"GIF89a",),
    (b"RIFF", b"WEBP"),
)


def is_image_bytes(head: bytes) -> bool:
    # HEIC/HEIF (and AVIF) are ISO-BMFF containers: the `ftyp` box sits at offset 4.
    if head[4:8] == b"ftyp":
        return True
    for parts in _IMAGE_SIGNATURES:
        if len(parts) == 1:
            if head.startswith(parts[0]):
                return True
        elif head.startswith(parts[0]) and head[8:12] == parts[1]:
            return True
    return False


def _image_block_head(block: Any) -> bytes | None:
    if not isinstance(block, dict) or block.get("type") != "image":
        return None
    encoded = block.get("base64")
    if not isinstance(encoded, str):
        return None
    try:
        return base64.b64decode(encoded[:24] + "==")[:12]
    except binascii.Error, ValueError:
        return b""


def validate_read_file_message(message: ToolMessage) -> ToolMessage:
    if not isinstance(message.content, list):
        return message
    for block in message.content:
        head = _image_block_head(block)
        if head is None or is_image_bytes(head):
            continue
        path = message.additional_kwargs.get("read_file_path", "the file")
        return ToolMessage(
            content=(
                f"read_file: {path} has an image extension but its contents are not a "
                f"valid image (starts with {head[:8]!r}). It was not attached; inspect "
                "it as text or re-download it."
            ),
            name=message.name,
            tool_call_id=message.tool_call_id,
            status="error",
        )
    return message


def _message_image_count(message: BaseMessage) -> int:
    if not isinstance(message.content, list):
        return 0
    return sum(
        isinstance(block, dict) and block.get("type") == "image" for block in message.content
    )


def _image_count(messages: list[BaseMessage]) -> int:
    return sum(_message_image_count(message) for message in messages)


def _image_path(message: BaseMessage) -> str:
    path = message.additional_kwargs.get("read_file_path")
    return path if isinstance(path, str) else "the file"


def limit_image_blocks(messages: list[BaseMessage], budget: int) -> list[BaseMessage]:
    excess = _image_count(messages) - budget
    if excess <= 0:
        return messages
    limited: list[BaseMessage] = []
    for message in messages:
        if not isinstance(message.content, list):
            limited.append(message)
            continue
        content = list(message.content)
        for index, block in enumerate(content):
            if excess <= 0:
                break
            if isinstance(block, dict) and block.get("type") == "image":
                content[index] = {
                    "type": "text",
                    "text": (
                        f"[image dropped to stay under the provider's {budget}-image limit: "
                        f"{_image_path(message)}]"
                    ),
                }
                excess -= 1
        limited.append(message.model_copy(update={"content": content}))
    return limited


def _read_file_image_stub(message: ToolMessage, budget: int) -> ToolMessage:
    path = _image_path(message)
    return message.model_copy(
        update={
            "content": (
                f"read_file: {path} image omitted because the conversation already contains "
                f"the provider's {budget}-image limit."
            )
        }
    )


class ValidateImageReadsMiddleware(OpenSWEMiddleware):
    state_schema = AgentState

    def __init__(self, model_id: str | None = None) -> None:
        self._model_id = model_id

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        model_id = getattr(request.model, "model_id", None)
        if not isinstance(model_id, str):
            model_name = getattr(request.model, "model_name", None)
            if isinstance(model_name, str) and "fireworks" in type(request.model).__module__:
                model_id = f"fireworks:{model_name}"
        budget = model_image_budget(model_id) if isinstance(model_id, str) else None
        if budget is None and self._model_id is not None:
            budget = model_image_budget(self._model_id)
        if budget is None:
            return await handler(request)
        messages = limit_image_blocks(request.messages, budget)
        return await handler(request.override(messages=messages))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        tool_call = request.tool_call
        if isinstance(result, ToolMessage) and tool_call.get("name") == "read_file":
            result = validate_read_file_message(result)
            state = request.state if isinstance(request.state, dict) else {}
            messages = state.get("messages", [])
            budget = model_image_budget(self._model_id) if self._model_id else None
            if budget is not None and _image_count(messages) >= budget:
                if _message_image_count(result) > 0:
                    return _read_file_image_stub(result, budget)
        return result
