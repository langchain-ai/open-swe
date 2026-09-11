import base64

from langchain_core.messages import ToolMessage

from agent.middleware.validate_image_reads import validate_read_file_message

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
