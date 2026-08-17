from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from deepagents.backends import LocalShellBackend

from agent.utils import sandbox_downloads
from agent.utils.sandbox_downloads import (
    InvalidSandboxDownloadToken,
    SandboxDownloadError,
    create_sandbox_download_link,
    decode_sandbox_download_token,
    inspect_sandbox_file,
    stream_sandbox_file_chunks,
)


class FakeBackend:
    def __init__(
        self,
        *,
        size: int = 3,
        content: object | None = None,
        chunk_error: str | None = None,
        delay_chunk: bool = False,
        delay_download: bool = False,
        delay_delete: bool = False,
    ) -> None:
        self.size = size
        self.content = content
        self.chunk_error = chunk_error
        self.delay_chunk = delay_chunk
        self.delay_download = delay_download
        self.delay_delete = delay_delete
        self.command_started = asyncio.Event()
        self.command_release = asyncio.Event()
        self.download_started = asyncio.Event()
        self.download_release = asyncio.Event()
        self.delete_started = asyncio.Event()
        self.delete_release = asyncio.Event()
        self.deleted_paths: list[str] = []
        self.staged_path: str | None = None
        self.chunk_length = 0
        self.download_count = 0

    async def aexecute(self, command: str, *, timeout: int | None = None):
        assert timeout == 120
        encoded = command.split("base64.b64decode('", 1)[1].split("')", 1)[0]
        payload = json.loads(base64.b64decode(encoded).decode())
        if "staged_path" not in payload:
            return SimpleNamespace(
                output=json.dumps({"ok": True, "size": self.size, "signature": "file-signature"}),
                exit_code=0,
            )
        self.staged_path = payload["returned_path"]
        self.chunk_length = payload["length"]
        if self.delay_chunk:
            self.command_started.set()
            await self.command_release.wait()
        if self.chunk_error:
            return SimpleNamespace(
                output=json.dumps({"ok": False, "error": self.chunk_error}),
                exit_code=0,
            )
        return SimpleNamespace(
            output=json.dumps({"ok": True, "path": self.staged_path, "size": self.chunk_length}),
            exit_code=0,
        )

    async def adownload_files(self, paths: list[str]):
        assert paths == [self.staged_path]
        self.download_count += 1
        if self.delay_download:
            self.download_started.set()
            await self.download_release.wait()
        if self.content is not None:
            return [self.content]
        return [SimpleNamespace(content=b"x" * self.chunk_length, error=None)]

    async def adelete(self, path: str):
        if self.delay_delete:
            self.delete_started.set()
            await self.delete_release.wait()
        self.deleted_paths.append(path)
        return SimpleNamespace(error=None)


async def collect_chunks(backend, info) -> bytes:
    return b"".join([chunk async for chunk in stream_sandbox_file_chunks(backend, info)])


def test_sandbox_download_link_round_trip(monkeypatch) -> None:
    secret = "test-secret-that-is-at-least-32-bytes"
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", secret)
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example/")

    link = create_sandbox_download_link(
        "thread-1",
        "sandbox-1",
        "/workspace/artifact.bin",
    )
    token = link.url.rsplit("/", 1)[-1]
    claims = decode_sandbox_download_token(token)
    payload = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience="open-swe-sandbox-file",
    )

    assert link.url.startswith("https://dashboard.example/dashboard/api/sandbox-files/")
    assert claims.thread_id == "thread-1"
    assert claims.sandbox_id == "sandbox-1"
    assert claims.file_path == "/workspace/artifact.bin"
    assert "exp" not in payload
    assert "iat" not in payload


def test_sandbox_download_token_rejects_tampering(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-that-is-at-least-32-bytes")
    token = create_sandbox_download_link(
        "thread-1", "sandbox-1", "/workspace/artifact.bin"
    ).url.rsplit("/", 1)[-1]
    tampered = ("a" if token[0] != "a" else "b") + token[1:]

    with pytest.raises(InvalidSandboxDownloadToken):
        decode_sandbox_download_token(tampered)


@pytest.mark.parametrize(
    "path",
    ["artifact.bin", "/workspace/../secret", "/workspace//artifact.bin", "/"],
)
def test_inspect_sandbox_file_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(SandboxDownloadError):
        create_sandbox_download_link("thread-1", "sandbox-1", path)


async def test_inspect_sandbox_file_accepts_files_over_previous_limit() -> None:
    backend = FakeBackend(size=101 * 1024 * 1024)

    info = await inspect_sandbox_file(backend, "/workspace/artifact.bin")

    assert info.size == 101 * 1024 * 1024


async def test_stream_sandbox_file_with_local_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox_downloads, "SANDBOX_DOWNLOAD_CHUNK_BYTES", 4)
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"binary\x00content")
    backend = LocalShellBackend(root_dir=str(tmp_path), virtual_mode=True)
    info = await inspect_sandbox_file(backend, "/artifact.bin")

    content = await collect_chunks(backend, info)

    assert info.size == len(content)
    assert content == b"binary\x00content"


async def test_stream_sandbox_file_detects_same_size_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox_downloads, "SANDBOX_DOWNLOAD_CHUNK_BYTES", 4)
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"abcdefgh")
    backend = LocalShellBackend(root_dir=str(tmp_path), virtual_mode=True)
    info = await inspect_sandbox_file(backend, "/artifact.bin")
    chunks = stream_sandbox_file_chunks(backend, info)

    assert await anext(chunks) == b"abcd"
    original = artifact.stat()
    artifact.write_bytes(b"ABCDEFGH")
    os.utime(artifact, ns=(original.st_atime_ns, original.st_mtime_ns))

    with pytest.raises(SandboxDownloadError, match="changed during download"):
        await anext(chunks)


async def test_stream_sandbox_file_validates_empty_file(tmp_path: Path) -> None:
    artifact = tmp_path / "empty.bin"
    artifact.write_bytes(b"")
    backend = LocalShellBackend(root_dir=str(tmp_path), virtual_mode=True)
    info = await inspect_sandbox_file(backend, "/empty.bin")
    artifact.write_bytes(b"changed")

    with pytest.raises(SandboxDownloadError, match="changed during download"):
        await collect_chunks(backend, info)


async def test_stream_sandbox_file_defers_cleanup_after_cancellation() -> None:
    backend = FakeBackend(delay_chunk=True)
    info = await inspect_sandbox_file(backend, "/workspace/artifact.bin")
    chunks = stream_sandbox_file_chunks(backend, info)
    next_chunk = asyncio.ensure_future(anext(chunks))
    await backend.command_started.wait()

    next_chunk.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_chunk
    assert backend.deleted_paths == []

    backend.command_release.set()
    for _ in range(10):
        if backend.deleted_paths:
            break
        await asyncio.sleep(0)

    assert backend.deleted_paths == [backend.staged_path]


async def test_stream_sandbox_file_defers_cleanup_after_download_cancellation() -> None:
    backend = FakeBackend(delay_download=True)
    info = await inspect_sandbox_file(backend, "/workspace/artifact.bin")
    chunks = stream_sandbox_file_chunks(backend, info)
    next_chunk = asyncio.ensure_future(anext(chunks))
    await backend.download_started.wait()

    next_chunk.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_chunk
    for _ in range(10):
        if backend.deleted_paths:
            break
        await asyncio.sleep(0)

    assert backend.deleted_paths == [backend.staged_path]


async def test_stream_sandbox_file_continues_cleanup_after_delete_cancellation() -> None:
    backend = FakeBackend(delay_delete=True)
    info = await inspect_sandbox_file(backend, "/workspace/artifact.bin")
    chunks = stream_sandbox_file_chunks(backend, info)
    next_chunk = asyncio.ensure_future(anext(chunks))
    await backend.delete_started.wait()

    next_chunk.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_chunk
    assert backend.deleted_paths == []

    backend.delete_release.set()
    for _ in range(10):
        if backend.deleted_paths:
            break
        await asyncio.sleep(0)

    assert backend.deleted_paths == [backend.staged_path]


async def test_stream_sandbox_file_uses_bounded_chunks(monkeypatch) -> None:
    monkeypatch.setattr(sandbox_downloads, "SANDBOX_DOWNLOAD_CHUNK_BYTES", 2)
    backend = FakeBackend(size=5)
    info = await inspect_sandbox_file(backend, "/workspace/artifact.bin")

    content = await collect_chunks(backend, info)

    assert content == b"xxxxx"
    assert backend.download_count == 3
    assert len(backend.deleted_paths) == 3
    assert len(set(backend.deleted_paths)) == 3


async def test_stream_sandbox_file_reads_nested_provider_content() -> None:
    backend = FakeBackend(content={"file_data": {"content": "abc"}})
    info = await inspect_sandbox_file(backend, "/workspace/artifact.bin")

    content = await collect_chunks(backend, info)

    assert content == b"abc"
    assert backend.deleted_paths == [backend.staged_path]


async def test_stream_sandbox_file_rejects_source_changes() -> None:
    backend = FakeBackend(chunk_error="changed")
    info = await inspect_sandbox_file(backend, "/workspace/artifact.bin")

    with pytest.raises(SandboxDownloadError, match="changed during download"):
        await collect_chunks(backend, info)

    assert backend.deleted_paths == [backend.staged_path]


async def test_stream_sandbox_file_handles_chunk_io_error() -> None:
    backend = FakeBackend(chunk_error="io_error")
    info = await inspect_sandbox_file(backend, "/workspace/artifact.bin")

    with pytest.raises(SandboxDownloadError, match="chunk could not be staged"):
        await collect_chunks(backend, info)

    assert backend.deleted_paths == [backend.staged_path]


async def test_stream_sandbox_file_handles_provider_error() -> None:
    backend = FakeBackend(content=SimpleNamespace(error="permission_denied", content=None))
    info = await inspect_sandbox_file(backend, "/workspace/artifact.bin")

    with pytest.raises(SandboxDownloadError, match="chunk could not be downloaded"):
        await collect_chunks(backend, info)

    assert backend.deleted_paths == [backend.staged_path]
