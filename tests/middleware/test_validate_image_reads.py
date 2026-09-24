import base64
from typing import cast

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest, ToolRuntime

from agent.middleware.validate_image_reads import (
    ValidateImageReadsMiddleware,
    limit_image_blocks,
    validate_read_file_message,
)

PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _read_file_image(payload: bytes, mime_type: str = "image/png") -> ToolMessage:
    return ToolMessage(
        content_blocks=[
            {"type": "image", "base64": base64.b64encode(payload).decode(), "mime_type": mime_type}
        ],
        name="read_file",
        tool_call_id="call-1",
        additional_kwargs={"read_file_path": "/tmp/pr-assets/offload.png"},
    )


def test_json_body_saved_as_png_becomes_text_error() -> None:
    # Production trace: an expired download link wrote a JSON error body to offload.png.
    message = _read_file_image(b'{"detail":{"error":"Download link is not valid"}}')

    result = validate_read_file_message(message)

    assert isinstance(result.content, str)
    assert "/tmp/pr-assets/offload.png" in result.content
    assert result.status == "error"
    assert result.tool_call_id == "call-1"


def test_real_image_bytes_pass_through_unchanged() -> None:
    message = _read_file_image(PNG_HEAD)

    assert validate_read_file_message(message) is message


def test_webp_jpeg_and_heic_are_recognised() -> None:
    webp = b"RIFF\x00\x00\x00\x00WEBPVP8 "
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 8
    heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00"
    for payload in (webp, jpeg, heic):
        message = _read_file_image(payload, mime_type="image/heic")
        assert validate_read_file_message(message) is message


def test_text_reads_are_untouched() -> None:
    message = ToolMessage(content="1\thello", name="read_file", tool_call_id="call-2")

    assert validate_read_file_message(message) is message


def test_image_blocks_over_budget_drop_oldest_images() -> None:
    messages = [
        _read_file_image(PNG_HEAD).model_copy(
            update={"additional_kwargs": {"read_file_path": f"/tmp/{index}.png"}}
        )
        for index in range(61)
    ]

    result = limit_image_blocks(messages, 60)

    assert (
        sum(block.get("type") == "image" for message in result for block in message.content) == 60
    )
    assert result[0].content[0]["type"] == "text"
    assert result[60].content[0]["type"] == "image"


def test_image_blocks_under_budget_are_untouched() -> None:
    messages = [_read_file_image(PNG_HEAD) for _ in range(60)]

    assert limit_image_blocks(messages, 60) == messages


@pytest.mark.asyncio
async def test_read_file_at_image_budget_returns_text_stub() -> None:
    middleware = ValidateImageReadsMiddleware(
        model_id="fireworks:accounts/fireworks/models/glm-5p3-flash"
    )
    existing = [_read_file_image(PNG_HEAD) for _ in range(60)]
    request = ToolCallRequest(
        tool_call={"name": "read_file", "args": {}, "id": "call-2", "type": "tool_call"},
        tool=None,
        state={"messages": existing},
        runtime=cast(ToolRuntime, None),
    )

    async def handler(_: ToolCallRequest) -> ToolMessage:
        return _read_file_image(PNG_HEAD)

    result = await middleware.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert isinstance(result.content, str)
    assert "60-image limit" in result.content


@pytest.mark.asyncio
async def test_model_call_keeps_newest_images_under_budget() -> None:
    middleware = ValidateImageReadsMiddleware()
    messages = [
        _read_file_image(PNG_HEAD).model_copy(
            update={"additional_kwargs": {"read_file_path": f"/tmp/{index}.png"}}
        )
        for index in range(61)
    ]
    request = ModelRequest(
        model=cast(BaseChatModel, type("FireworksModel", (), {"model_id": "fireworks:test"})()),
        messages=messages,
    )
    seen: list[ModelRequest] = []

    async def handler(updated: ModelRequest) -> ModelResponse:
        seen.append(updated)
        return ModelResponse(result=[])

    await middleware.awrap_model_call(request, handler)

    assert len(seen) == 1
    assert (
        sum(
            block.get("type") == "image"
            for message in seen[0].messages
            for block in message.content
            if isinstance(message.content, list)
        )
        == 60
    )
