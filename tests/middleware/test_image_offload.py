import base64
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from deepagents.backends.protocol import FileDownloadResponse, FileUploadResponse
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware import image_offload
from agent.middleware.image_offload import IMAGE_UNAVAILABLE_TEXT, ImageOffloadMiddleware
from agent.thread_images import LocalImageStore, SandboxImageStore

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 40
PNG = base64.b64encode(PNG_BYTES).decode()
IMAGE_ID = "0" * 32


@pytest.fixture
def stores(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, LocalImageStore]:
    by_thread: dict[str, LocalImageStore] = {}

    async def open_store(thread_id: str, *, desktop: bool = False) -> LocalImageStore:
        return by_thread.setdefault(thread_id, LocalImageStore(tmp_path / thread_id))

    monkeypatch.setattr(image_offload, "open_image_store", open_store)
    return by_thread


def _config(thread_id: str, **extra: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id, **extra}}


def _request(messages: list[Any]) -> ModelRequest:
    return ModelRequest(
        model=object(),  # type: ignore[arg-type]
        messages=messages,
        state={"messages": messages},
        runtime=object(),  # type: ignore[arg-type]
    )


def _with_config(config: dict[str, Any]) -> Any:
    return patch("agent.run_config.get_config", return_value=config)


async def test_before_model_moves_inline_images_to_the_thread_store(
    stores: dict[str, LocalImageStore], tmp_path: Path
) -> None:
    human = HumanMessage(
        id="m1",
        content=[
            {"type": "image", "base64": PNG, "mime_type": "image/png", "file_name": "a.png"},
            {"type": "text", "text": "what is this"},
        ],
    )
    plain = HumanMessage(id="m0", content="hello")

    with _with_config(_config("thread-1")):
        update = await ImageOffloadMiddleware().abefore_model(
            {"messages": [plain, human]},
            object(),  # type: ignore[arg-type]
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
    stored = tmp_path / "thread-1" / f"{image_block['file_id']}.png"
    assert stored.read_bytes() == PNG_BYTES


async def test_images_the_store_cannot_take_stay_inline(
    stores: dict[str, LocalImageStore],
) -> None:
    human = HumanMessage(
        id="m1",
        content=[
            {"type": "image", "base64": PNG, "mime_type": "image/svg+xml"},
            {"type": "image", "base64": "%%%not base64", "mime_type": "image/png"},
        ],
    )

    with _with_config(_config("thread-1")):
        update = await ImageOffloadMiddleware().abefore_model(
            {"messages": [human]},
            object(),  # type: ignore[arg-type]
        )

    assert update is None


async def test_model_call_sees_the_bytes_while_state_keeps_the_reference(
    stores: dict[str, LocalImageStore], tmp_path: Path
) -> None:
    await LocalImageStore(tmp_path / "thread-1").put(f"{IMAGE_ID}.png", PNG_BYTES)
    human = HumanMessage(
        id="m1", content=[{"type": "image", "file_id": IMAGE_ID, "mime_type": "image/png"}]
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

    with _with_config(_config("thread-1")):
        await ImageOffloadMiddleware().awrap_model_call(_request([human, tool]), handler)

    rehydrated_human, rehydrated_tool = seen
    assert rehydrated_human.content == [{"type": "image", "base64": PNG, "mime_type": "image/png"}]
    assert rehydrated_tool.content == [{"type": "text", "text": IMAGE_UNAVAILABLE_TEXT}]
    assert human.content[0] == {"type": "image", "file_id": IMAGE_ID, "mime_type": "image/png"}


async def _model_saw(middleware: ImageOffloadMiddleware, config: dict[str, Any]) -> list[Any]:
    human = HumanMessage(
        id="m1", content=[{"type": "image", "file_id": IMAGE_ID, "mime_type": "image/png"}]
    )
    seen: list[Any] = []

    async def handler(request: ModelRequest) -> Any:
        seen.extend(request.messages)
        return AIMessage(content="ok")

    with _with_config(config):
        await middleware.awrap_model_call(_request([human]), handler)
    return seen[0].content


async def test_only_threads_that_may_show_an_image_get_its_bytes(
    stores: dict[str, LocalImageStore], tmp_path: Path
) -> None:
    await LocalImageStore(tmp_path / "victim").put(f"{IMAGE_ID}.png", PNG_BYTES)
    middleware = ImageOffloadMiddleware()
    image = [{"type": "image", "base64": PNG, "mime_type": "image/png"}]
    unavailable = [{"type": "text", "text": IMAGE_UNAVAILABLE_TEXT}]

    assert await _model_saw(middleware, _config("victim")) == image
    # Already cached for the victim's thread, which must not leak it to another.
    assert await _model_saw(middleware, _config("attacker")) == unavailable
    continued = _config("continued", continued_from_thread_id="victim")
    assert await _model_saw(middleware, continued) == image


class _FakeSandbox:
    """Just enough of a sandbox backend to hold uploaded files."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.commands: list[str] = []

    async def aexecute(self, command: str, *, timeout: int | None = None) -> object:
        self.commands.append(command)
        return object()

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        self.files.update(dict(files))
        return [FileUploadResponse(path=path, error=None) for path, _ in files]

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [
            FileDownloadResponse(path=path, content=self.files.get(path), error=None)
            for path in paths
        ]


async def test_sandbox_store_keeps_images_under_the_work_dir() -> None:
    sandbox = _FakeSandbox()
    store = SandboxImageStore(sandbox, "/workspace")  # type: ignore[arg-type]

    await store.put(f"{IMAGE_ID}.png", PNG_BYTES)

    assert sandbox.files == {f"/workspace/.open-swe/images/{IMAGE_ID}.png": PNG_BYTES}
    assert sandbox.commands == ["mkdir -p /workspace/.open-swe/images"]
    assert await store.get(f"{IMAGE_ID}.png") == PNG_BYTES
    assert await store.get(f"{'f' * 32}.png") is None
