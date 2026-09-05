import base64
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage

import agent.media as media
from agent.input_messages import human_input
from agent.media import MediaRef, media_data
from agent.middleware.media_hydration import MediaHydrationMiddleware

_VISION_MODEL = "openai:gpt-5.6-sol"
_TEXT_ONLY_MODEL = "fireworks:accounts/fireworks/models/deepseek-v4-pro"
_REF = MediaRef(
    path="/uploads/" + "c" * 64 + ".png",
    mime_type="image/png",
    sha256="c" * 64,
    size=3,
)


@pytest.fixture(autouse=True)
def _empty_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "_CACHE", type(media._CACHE)())
    monkeypatch.setattr(media, "_CACHE_BYTES", 0)


def _backend(content: bytes | None = b"png") -> Any:
    backend = MagicMock(spec=SandboxBackendProtocol)
    backend.adownload_files = AsyncMock(
        side_effect=lambda paths: [
            SimpleNamespace(path=path, content=content, error=None if content else "file_not_found")
            for path in paths
        ]
    )
    return backend


def _envelope(refs: list[MediaRef]) -> str:
    message = human_input(
        "see attached",
        {"sender_id": "github:alice", "surface": "web", "kind": "human", "data": media_data(refs)},
    )
    assert isinstance(message["content"], str)
    return message["content"]


def _request(messages: list[Any]) -> ModelRequest:
    return cast(ModelRequest, SimpleNamespace(messages=messages, override=_override))


def _override(**overrides: Any) -> Any:
    return SimpleNamespace(**overrides)


async def _hydrated(middleware: MediaHydrationMiddleware, messages: list[Any]) -> list[Any]:
    seen: list[Any] = []

    async def handler(request: Any) -> Any:
        seen.append(request)
        return AIMessage(content="ok")

    await middleware.awrap_model_call(_request(messages), handler)
    return seen[0].messages


async def test_hydration_attaches_image_blocks_only_to_the_provider_copy() -> None:
    backend = _backend()
    original = HumanMessage(content=_envelope([_REF]), id="m1")
    middleware = MediaHydrationMiddleware(backend, model_id=_VISION_MODEL)

    messages = await _hydrated(middleware, [original])

    hydrated = messages[0]
    assert hydrated.id == "m1"
    assert hydrated.content == [
        {"type": "text", "text": original.content},
        {"type": "text", "text": f"Attachment `{_REF.path}`:"},
        {"type": "image", "base64": base64.b64encode(b"png").decode(), "mime_type": "image/png"},
    ]
    assert isinstance(original.content, str)


async def test_hydration_captions_each_image_with_its_path() -> None:
    second = _REF.model_copy(update={"path": "/uploads/" + "d" * 64 + "-other.png"})
    middleware = MediaHydrationMiddleware(_backend(), model_id=_VISION_MODEL)

    messages = await _hydrated(middleware, [HumanMessage(content=_envelope([_REF, second]))])

    kinds = [(block["type"], block.get("text")) for block in messages[0].content[1:]]
    assert kinds == [
        ("text", f"Attachment `{_REF.path}`:"),
        ("image", None),
        ("text", f"Attachment `{second.path}`:"),
        ("image", None),
    ]


async def test_hydration_leaves_requests_without_media_untouched() -> None:
    backend = _backend()
    middleware = MediaHydrationMiddleware(backend, model_id=_VISION_MODEL)
    plain = HumanMessage(content="hello")
    seen: list[Any] = []

    async def handler(request: Any) -> Any:
        seen.append(request)
        return AIMessage(content="ok")

    request = _request([plain, AIMessage(content="hi")])
    await middleware.awrap_model_call(request, handler)

    assert seen[0] is request
    backend.adownload_files.assert_not_awaited()


async def test_hydration_withholds_images_from_text_only_models() -> None:
    backend = _backend()
    middleware = MediaHydrationMiddleware(backend, model_id=_TEXT_ONLY_MODEL)

    messages = await _hydrated(middleware, [HumanMessage(content=_envelope([_REF]))])

    note = messages[0].content[-1]
    assert note["type"] == "text"
    assert "text-only" in note["text"]
    backend.adownload_files.assert_not_awaited()


async def test_hydration_notes_a_missing_attachment_instead_of_failing() -> None:
    middleware = MediaHydrationMiddleware(_backend(content=None), model_id=_VISION_MODEL)

    messages = await _hydrated(middleware, [HumanMessage(content=_envelope([_REF]))])

    note = messages[0].content[-1]
    assert note["type"] == "text"
    assert "no longer available" in note["text"]


async def test_hydration_downloads_each_attachment_once_across_turns() -> None:
    backend = _backend()
    middleware = MediaHydrationMiddleware(backend, model_id=_VISION_MODEL)
    history = [HumanMessage(content=_envelope([_REF]))]

    await _hydrated(middleware, history)
    await _hydrated(middleware, [*history, AIMessage(content="working")])

    assert backend.adownload_files.await_count == 1
