import base64
from typing import Any
from unittest.mock import patch

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.store.memory import InMemoryStore

from agent.middleware.image_offload import (
    IMAGE_STORE_NAMESPACE,
    IMAGE_UNAVAILABLE_TEXT,
    ImageOffloadMiddleware,
)

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 40).decode()


def _runtime(store: InMemoryStore | None) -> Any:
    class _Runtime:
        def __init__(self) -> None:
            self.store = store

    return _Runtime()


def _config(thread_id: str, **extra: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id, **extra}}


def _request(messages: list[Any], store: InMemoryStore) -> ModelRequest:
    return ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=messages,
        state={"messages": messages},
        runtime=_runtime(store),
    )


async def test_before_model_moves_inline_images_to_the_store() -> None:
    store = InMemoryStore()
    human = HumanMessage(
        id="m1",
        content=[
            {"type": "image", "base64": PNG, "mime_type": "image/png", "file_name": "a.png"},
            {"type": "text", "text": "what is this"},
        ],
    )
    plain = HumanMessage(id="m0", content="hello")

    with patch("agent.middleware.image_offload.get_config", return_value=_config("thread-1")):
        update = await ImageOffloadMiddleware().abefore_model(
            {"messages": [plain, human]}, _runtime(store)
        )

    assert update is not None
    replaced_messages = update["messages"]
    assert isinstance(replaced_messages, list)
    [replaced] = replaced_messages
    assert replaced.id == "m1"
    image_block, text_block = replaced.content
    assert "base64" not in image_block
    assert image_block["mime_type"] == "image/png"
    assert image_block["file_name"] == "a.png"
    assert text_block == {"type": "text", "text": "what is this"}
    item = await store.aget(IMAGE_STORE_NAMESPACE, image_block["file_id"])
    assert item is not None
    assert item.value["base64"] == PNG
    assert item.value["thread_id"] == "thread-1"
    assert item.value["bytes"] == 48


async def test_before_model_is_a_no_op_without_inline_images() -> None:
    messages = [
        HumanMessage(id="m0", content="hello"),
        HumanMessage(
            id="m1", content=[{"type": "image", "file_id": "abc", "mime_type": "image/png"}]
        ),
        AIMessage(id="m2", content="hi"),
    ]
    assert (
        await ImageOffloadMiddleware().abefore_model({"messages": messages}, _runtime(None)) is None
    )


async def test_model_call_sees_the_bytes_while_state_keeps_the_reference() -> None:
    store = InMemoryStore()
    await store.aput(
        IMAGE_STORE_NAMESPACE,
        "0" * 32,
        {"thread_id": "thread-1", "mime_type": "image/png", "base64": PNG, "bytes": 48},
    )
    human = HumanMessage(
        id="m1",
        content=[{"type": "image", "file_id": "0" * 32, "mime_type": "image/png"}],
    )
    tool = ToolMessage(
        id="t1",
        tool_call_id="call-1",
        content=[{"type": "image", "file_id": "f" * 32, "mime_type": "image/png"}],
    )
    seen: list[Any] = []

    async def handler(request: ModelRequest) -> Any:
        seen.extend(request.messages)
        return AIMessage(content="ok")

    with patch("agent.middleware.image_offload.get_config", return_value=_config("thread-1")):
        await ImageOffloadMiddleware().awrap_model_call(_request([human, tool], store), handler)

    rehydrated_human, rehydrated_tool = seen
    assert rehydrated_human.content == [{"type": "image", "base64": PNG, "mime_type": "image/png"}]
    assert rehydrated_tool.content == [{"type": "text", "text": IMAGE_UNAVAILABLE_TEXT}]
    assert human.content[0] == {"type": "image", "file_id": "0" * 32, "mime_type": "image/png"}


async def _model_saw(
    middleware: ImageOffloadMiddleware, store: InMemoryStore, config: dict[str, Any]
) -> list[Any]:
    human = HumanMessage(
        id="m1", content=[{"type": "image", "file_id": "0" * 32, "mime_type": "image/png"}]
    )
    seen: list[Any] = []

    async def handler(request: ModelRequest) -> Any:
        seen.extend(request.messages)
        return AIMessage(content="ok")

    with patch("agent.middleware.image_offload.get_config", return_value=config):
        await middleware.awrap_model_call(_request([human], store), handler)
    return seen[0].content


async def test_a_reference_to_another_threads_image_is_not_rehydrated() -> None:
    store = InMemoryStore()
    await store.aput(
        IMAGE_STORE_NAMESPACE,
        "0" * 32,
        {"thread_id": "victim", "mime_type": "image/png", "base64": PNG, "bytes": 48},
    )
    middleware = ImageOffloadMiddleware()

    assert await _model_saw(middleware, store, _config("victim")) == [
        {"type": "image", "base64": PNG, "mime_type": "image/png"}
    ]
    # Already cached for the victim's thread, which must not leak it to another.
    assert await _model_saw(middleware, store, _config("attacker")) == [
        {"type": "text", "text": IMAGE_UNAVAILABLE_TEXT}
    ]
    assert await _model_saw(middleware, store, {"configurable": {}}) == [
        {"type": "text", "text": IMAGE_UNAVAILABLE_TEXT}
    ]


async def test_a_private_continuation_rehydrates_the_images_it_copied() -> None:
    store = InMemoryStore()
    await store.aput(
        IMAGE_STORE_NAMESPACE,
        "0" * 32,
        {"thread_id": "source", "mime_type": "image/png", "base64": PNG, "bytes": 48},
    )

    content = await _model_saw(
        ImageOffloadMiddleware(),
        store,
        _config("continued", continued_from_thread_id="source"),
    )

    assert content == [{"type": "image", "base64": PNG, "mime_type": "image/png"}]
