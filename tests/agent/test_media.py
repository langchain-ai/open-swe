import hashlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from deepagents.backends.protocol import SandboxBackendProtocol

import agent.media as media
from agent.input_messages import human_input
from agent.media import (
    MediaError,
    MediaRef,
    MediaUpload,
    download_media,
    media_data,
    media_file_name,
    media_mime_type,
    media_refs_from_content,
    store_media,
)

_PNG = MediaUpload(data=b"png bytes", mime_type="image/png", file_name="shot.png")
_PNG_SHA = hashlib.sha256(_PNG.data).hexdigest()


def _backend(*, upload_error: str | None = None, download_error: str | None = None) -> Any:
    backend = MagicMock(spec=SandboxBackendProtocol)
    backend.get_work_dir = lambda: "/workspace"
    backend.aupload_files = AsyncMock(
        side_effect=lambda files: [
            SimpleNamespace(path=path, error=upload_error) for path, _ in files
        ]
    )
    backend.adownload_files = AsyncMock(
        side_effect=lambda paths: [
            SimpleNamespace(
                path=path,
                content=None if download_error else b"png bytes",
                error=download_error,
            )
            for path in paths
        ]
    )
    return backend


@pytest.fixture(autouse=True)
def _empty_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "_CACHE", type(media._CACHE)())
    monkeypatch.setattr(media, "_CACHE_BYTES", 0)


async def test_store_media_is_content_addressed_beside_the_checkout() -> None:
    backend = _backend()

    refs = await store_media(backend, [_PNG, _PNG])

    path = f"/uploads/{_PNG_SHA}-shot.png"
    assert [ref.path for ref in refs] == [path, path]
    assert refs[0] == MediaRef(
        path=path, mime_type="image/png", sha256=_PNG_SHA, size=len(_PNG.data), file_name="shot.png"
    )
    assert backend.aupload_files.await_args.args == ([(path, _PNG.data)],)


async def test_store_media_raises_when_the_sandbox_rejects_the_upload() -> None:
    with pytest.raises(MediaError, match="permission_denied"):
        await store_media(_backend(upload_error="permission_denied"), [_PNG])


async def test_media_refs_round_trip_through_the_envelope() -> None:
    refs = await store_media(_backend(), [_PNG])
    message = human_input(
        "look at <this>",
        {"sender_id": "github:alice", "surface": "web", "kind": "human", "data": media_data(refs)},
    )

    assert media_refs_from_content(message["content"]) == refs
    assert media_refs_from_content([{"type": "text", "text": message["content"]}]) == refs
    assert media_refs_from_content("plain text") == []
    assert media_data([]) == {}


async def test_download_media_caches_by_path() -> None:
    backend = _backend()
    path = f"/uploads/{_PNG_SHA}.png"

    assert await download_media(backend, path) == b"png bytes"
    assert await download_media(backend, path) == b"png bytes"

    assert backend.adownload_files.await_count == 1


async def test_download_media_returns_none_for_a_missing_file() -> None:
    backend = _backend(download_error="file_not_found")

    assert await download_media(backend, "/uploads/missing.png") is None


def test_media_mime_type_only_accepts_stored_file_names() -> None:
    assert media_mime_type(f"{_PNG_SHA}.png") == "image/png"
    assert media_mime_type(f"{_PNG_SHA}-Screen-shot.jpg") == "image/jpeg"
    assert media_mime_type("../etc/passwd") is None
    assert media_mime_type(f"{_PNG_SHA}-../x.png") is None
    assert media_mime_type(f"{_PNG_SHA}.svg") is None
    assert media_mime_type("short.png") is None


def test_media_file_name_keeps_a_readable_stem_without_trusting_it() -> None:
    assert media_file_name("a" * 64, "image/png") == "a" * 64 + ".png"
    assert media_file_name("a" * 64, "image/jpeg", "My Screen shot.jpeg") == (
        "a" * 64 + "-My-Screen-shot.jpg"
    )
    assert media_file_name("a" * 64, "image/png", "../../etc/passwd") == "a" * 64 + "-passwd.png"
    assert media_file_name("a" * 64, "image/png", "x" * 200 + ".png").endswith(
        "-" + "x" * 64 + ".png"
    )
