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

from langchain.agents.middleware.types import AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

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


class ValidateImageReadsMiddleware(OpenSWEMiddleware):
    state_schema = AgentState

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        tool_call = request.tool_call
        if isinstance(result, ToolMessage) and tool_call.get("name") == "read_file":
            return validate_read_file_message(result)
        return result
