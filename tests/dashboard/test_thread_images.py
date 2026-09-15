import base64
from typing import Any
from unittest.mock import AsyncMock

import pytest
from conftest import patch_thread_module
from fastapi import HTTPException

from agent.threads import images

IMAGE_ID = "a" * 32
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 8


def _stored(**overrides: Any) -> dict[str, Any]:
    return {
        "thread_id": "thread-1",
        "mime_type": "image/png",
        "base64": base64.b64encode(PNG_BYTES).decode(),
        "file_name": "shot (1).png",
        **overrides,
    }


@pytest.fixture
def readable(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_thread_module(monkeypatch, "_readable_thread_metadata", AsyncMock(return_value={}))


async def test_serves_the_stored_bytes_with_its_content_type(
    monkeypatch: pytest.MonkeyPatch, readable: None
) -> None:
    monkeypatch.setattr(images, "get_value", AsyncMock(return_value=_stored()))

    response = await images.get_dashboard_thread_image("thread-1", IMAGE_ID, "alice")

    assert response.body == PNG_BYTES
    assert response.media_type == "image/png"
    assert response.headers["content-disposition"] == 'inline; filename="shot-1-.png"'
    assert "immutable" in response.headers["cache-control"]


@pytest.mark.parametrize(
    ("image_id", "stored"),
    [
        ("not-an-id", _stored()),
        (IMAGE_ID, None),
        (IMAGE_ID, _stored(mime_type="text/html")),
        (IMAGE_ID, _stored(base64="%%%")),
        (IMAGE_ID, _stored(thread_id="")),
    ],
)
async def test_unusable_images_are_not_found(
    monkeypatch: pytest.MonkeyPatch, readable: None, image_id: str, stored: dict[str, Any] | None
) -> None:
    monkeypatch.setattr(images, "get_value", AsyncMock(return_value=stored))

    with pytest.raises(HTTPException) as exc:
        await images.get_dashboard_thread_image("thread-1", image_id, "alice")
    assert exc.value.status_code == 404


async def test_images_of_other_threads_need_that_thread_to_be_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked: list[str] = []

    async def readable(thread_id: str, **_: object) -> dict[str, Any]:
        checked.append(thread_id)
        if thread_id != "thread-1":
            raise HTTPException(404, "thread not found")
        return {}

    patch_thread_module(monkeypatch, "_readable_thread_metadata", readable)
    monkeypatch.setattr(images, "get_value", AsyncMock(return_value=_stored(thread_id="other")))

    with pytest.raises(HTTPException) as exc:
        await images.get_dashboard_thread_image("thread-1", IMAGE_ID, "alice")
    assert exc.value.status_code == 404
    assert checked == ["thread-1", "other"]


async def test_unreadable_threads_hide_their_images(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_thread_module(
        monkeypatch,
        "_readable_thread_metadata",
        AsyncMock(side_effect=HTTPException(404, "thread not found")),
    )
    get_value = AsyncMock(return_value=_stored())
    monkeypatch.setattr(images, "get_value", get_value)

    with pytest.raises(HTTPException):
        await images.get_dashboard_thread_image("thread-1", IMAGE_ID, "mallory")
    get_value.assert_not_awaited()
