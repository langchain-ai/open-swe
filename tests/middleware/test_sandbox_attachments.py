import base64
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from deepagents.backends.protocol import BackendProtocol, FileUploadResponse
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from openswe.middleware.sandbox_attachments import SandboxAttachmentsMiddleware


def _encoded(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


async def test_attachments_reach_the_model_as_sandbox_paths_written_once() -> None:
    uploads: list[list[tuple[str, bytes]]] = []

    async def upload(files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        uploads.append(files)
        return [FileUploadResponse(path=path) for path, _ in files]

    backend = MagicMock()
    backend.aupload_files = upload
    middleware = SandboxAttachmentsMiddleware(cast(BackendProtocol, backend))
    message = HumanMessage(
        id="msg-1",
        content=[
            {"type": "text", "text": "see attached"},
            {
                "type": "file",
                "base64": _encoded(b"<html></html>"),
                "mime_type": "text/html",
                "file_name": "report.html",
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{_encoded(b'png')}"},
            },
        ],
    )
    handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="ok")]))
    request = ModelRequest(model=cast(BaseChatModel, MagicMock()), messages=[message])

    await middleware.awrap_model_call(request, handler)
    await middleware.awrap_model_call(request, handler)

    assert uploads == [
        [
            ("/tmp/open-swe/attachments/msg-1/report.html", b"<html></html>"),
            ("/tmp/open-swe/attachments/msg-1/attachment-2.png", b"png"),
        ]
    ]
    sent = handler.await_args.args[0].messages[0].content
    assert sent[0] == {"type": "text", "text": "see attached"}
    assert all(block["type"] == "text" for block in sent)
    assert 'path="/tmp/open-swe/attachments/msg-1/report.html"' in sent[1]["text"]
    assert 'name="report.html"' in sent[1]["text"]
    assert 'path="/tmp/open-swe/attachments/msg-1/attachment-2.png"' in sent[2]["text"]
    assert message.content[1]["type"] == "file"


async def test_a_failed_write_is_reported_and_retried() -> None:
    backend = MagicMock()
    backend.aupload_files = AsyncMock(
        side_effect=[
            [FileUploadResponse(path="p", error="permission_denied")],
            [FileUploadResponse(path="p", error=None)],
        ]
    )
    middleware = SandboxAttachmentsMiddleware(cast(BackendProtocol, backend))
    message = HumanMessage(
        id="msg-2",
        content=[{"type": "file", "base64": _encoded(b"x"), "mime_type": "text/plain"}],
    )
    handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="ok")]))
    request = ModelRequest(model=cast(BaseChatModel, MagicMock()), messages=[message])

    await middleware.awrap_model_call(request, handler)
    assert "permission_denied" in handler.await_args.args[0].messages[0].content[0]["text"]

    await middleware.awrap_model_call(request, handler)
    assert "error=" not in handler.await_args.args[0].messages[0].content[0]["text"]
    assert backend.aupload_files.await_count == 2
