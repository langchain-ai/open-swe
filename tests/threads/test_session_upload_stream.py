import gzip

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.types import Message

from openswe.threads.session_upload import UploadStream


def _request(body: bytes, *, chunk: int, encoding: str | None) -> Request:
    parts = [body[index : index + chunk] for index in range(0, len(body), chunk)] or [b""]
    messages: list[Message] = [
        {"type": "http.request", "body": part, "more_body": index < len(parts) - 1}
        for index, part in enumerate(parts)
    ]

    async def receive() -> Message:
        return messages.pop(0)

    headers = [(b"content-encoding", encoding.encode())] if encoding else []
    return Request({"type": "http", "method": "POST", "headers": headers}, receive)


async def _lines(request: Request) -> list[str]:
    return [line async for line in UploadStream(request).lines()]


@pytest.mark.parametrize("encoding", ["gzip", None])
async def test_lines_survive_any_chunking(encoding: str | None) -> None:
    text = '{"type":"claude"}\n{"text":"héllo ✓"}\n\n{"last":true}'
    raw = text.encode()
    body = gzip.compress(raw) if encoding else raw

    assert await _lines(_request(body, chunk=3, encoding=encoding)) == text.split("\n")


@pytest.mark.parametrize(
    "body",
    [
        gzip.compress(b'{"type":"claude"}\n' * 100)[:-8],
        gzip.compress(b'{"type":"claude"}\n') + b"trailing",
    ],
    ids=["missing-trailer", "trailing-bytes"],
)
async def test_gzip_that_is_not_exactly_one_stream_is_rejected(body: bytes) -> None:
    with pytest.raises(HTTPException) as raised:
        await _lines(_request(body, chunk=64, encoding="gzip"))
    assert raised.value.status_code == 400
