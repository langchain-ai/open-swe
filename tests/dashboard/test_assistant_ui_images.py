import pytest
from fastapi import HTTPException

from openswe.threads.runs import _command_attachments


@pytest.mark.parametrize(
    "url", ["data:image/png;base64,aGVsbG8=", {"url": "data:image/png;base64,aGVsbG8="}]
)
def test_native_image_blocks_become_attachments(url: str | dict[str, str]) -> None:
    text = {"type": "text", "text": "see this"}
    content, attachments = _command_attachments([{"type": "image_url", "image_url": url}, text])
    assert [(a.mime_type, a.data) for a in attachments] == [("image/png", b"hello")]
    assert content == [text]


@pytest.mark.parametrize(
    "url", ["https://example.com/image.png", "data:image/png,hello", "not an image"]
)
def test_native_image_blocks_reject_unsupported_sources(url: str) -> None:
    with pytest.raises(HTTPException) as failure:
        _command_attachments([{"type": "image_url", "image_url": {"url": url}}])
    assert failure.value.status_code == 422
