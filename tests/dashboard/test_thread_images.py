from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.thread_images import LocalImageStore
from agent.threads import images
from tests.conftest import patch_thread_module

IMAGE_NAME = "a" * 32 + ".png"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 8


@pytest.fixture
def stores(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, LocalImageStore]:
    """One on-disk image store per thread, opened by thread id."""
    by_thread: dict[str, LocalImageStore] = {}

    async def open_store(thread_id: str, *, desktop: bool = False) -> LocalImageStore:
        return by_thread.setdefault(thread_id, LocalImageStore(tmp_path / thread_id))

    monkeypatch.setattr(images, "open_image_store", open_store)
    return by_thread


@pytest.fixture
def readable(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_thread_module(monkeypatch, "_readable_thread_metadata", AsyncMock(return_value={}))


async def test_serves_the_stored_bytes_with_its_content_type(
    stores: dict[str, LocalImageStore], readable: None, tmp_path: Path
) -> None:
    await LocalImageStore(tmp_path / "thread-1").put(IMAGE_NAME, PNG_BYTES)

    response = await images.get_dashboard_thread_image("thread-1", IMAGE_NAME, "alice")

    assert response.body == PNG_BYTES
    assert response.media_type == "image/png"
    assert "immutable" in response.headers["cache-control"]


@pytest.mark.parametrize(
    "image_name",
    ["not-an-image", "a" * 32, "a" * 32 + ".svg", "../" + IMAGE_NAME, "b" * 32 + ".png"],
)
async def test_unknown_or_malformed_names_are_not_found(
    stores: dict[str, LocalImageStore], readable: None, tmp_path: Path, image_name: str
) -> None:
    await LocalImageStore(tmp_path / "thread-1").put(IMAGE_NAME, PNG_BYTES)

    with pytest.raises(HTTPException) as exc:
        await images.get_dashboard_thread_image("thread-1", image_name, "alice")
    assert exc.value.status_code == 404


async def test_a_private_continuation_falls_back_to_the_source_thread(
    monkeypatch: pytest.MonkeyPatch, stores: dict[str, LocalImageStore], tmp_path: Path
) -> None:
    patch_thread_module(
        monkeypatch,
        "_readable_thread_metadata",
        AsyncMock(return_value={"continued_from_thread_id": "source"}),
    )
    await LocalImageStore(tmp_path / "source").put(IMAGE_NAME, PNG_BYTES)

    response = await images.get_dashboard_thread_image("continued", IMAGE_NAME, "alice")

    assert response.body == PNG_BYTES


async def test_a_continuation_without_a_sandbox_still_falls_back_to_the_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    patch_thread_module(
        monkeypatch,
        "_readable_thread_metadata",
        AsyncMock(return_value={"continued_from_thread_id": "source"}),
    )
    source = LocalImageStore(tmp_path / "source")
    await source.put(IMAGE_NAME, PNG_BYTES)

    async def open_store(thread_id: str, *, desktop: bool = False) -> LocalImageStore:
        if thread_id == "continued":
            raise ValueError("Missing sandbox_id in thread metadata for continued")
        return source

    monkeypatch.setattr(images, "open_image_store", open_store)

    response = await images.get_dashboard_thread_image("continued", IMAGE_NAME, "alice")

    assert response.body == PNG_BYTES


async def test_unreadable_threads_hide_their_images(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_thread_module(
        monkeypatch,
        "_readable_thread_metadata",
        AsyncMock(side_effect=HTTPException(404, "thread not found")),
    )
    open_store = AsyncMock()
    monkeypatch.setattr(images, "open_image_store", open_store)

    with pytest.raises(HTTPException):
        await images.get_dashboard_thread_image("thread-1", IMAGE_NAME, "mallory")
    open_store.assert_not_awaited()


async def test_an_unreachable_workspace_is_a_503(
    monkeypatch: pytest.MonkeyPatch, readable: None
) -> None:
    monkeypatch.setattr(
        images, "open_image_store", AsyncMock(side_effect=RuntimeError("sandbox down"))
    )

    with pytest.raises(HTTPException) as exc:
        await images.get_dashboard_thread_image("thread-1", IMAGE_NAME, "alice")
    assert exc.value.status_code == 503
