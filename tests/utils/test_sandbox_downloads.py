from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from deepagents.backends import LocalShellBackend

from agent.utils.sandbox_downloads import (
    SANDBOX_DOWNLOAD_MAX_BYTES,
    InvalidSandboxDownloadToken,
    SandboxDownloadError,
    SandboxDownloadFileTooLarge,
    create_sandbox_download_link,
    decode_sandbox_download_token,
    download_sandbox_file,
    inspect_sandbox_file,
)


class FakeBackend:
    def __init__(
        self,
        *,
        size: int = 3,
        content: object = b"abc",
        stage_error: str | None = None,
    ) -> None:
        self.size = size
        self.content = content
        self.stage_error = stage_error
        self.deleted_paths: list[str] = []
        self.staged_path: str | None = None

    async def aexecute(self, command: str, *, timeout: int | None = None):
        assert timeout == 120
        encoded = command.split("base64.b64decode('", 1)[1].split("')", 1)[0]
        payload = json.loads(base64.b64decode(encoded).decode())
        if "staged_path" not in payload:
            return SimpleNamespace(
                output=json.dumps({"ok": True, "size": self.size}),
                exit_code=0,
            )
        self.staged_path = payload["returned_path"]
        if self.stage_error:
            return SimpleNamespace(
                output=json.dumps({"ok": False, "error": self.stage_error}),
                exit_code=0,
            )
        return SimpleNamespace(
            output=json.dumps({"ok": True, "path": self.staged_path, "size": self.size}),
            exit_code=0,
        )

    async def adownload_files(self, paths: list[str]):
        assert paths == [self.staged_path]
        return [self.content]

    async def adelete(self, path: str):
        self.deleted_paths.append(path)
        return SimpleNamespace(error=None)


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


async def test_inspect_sandbox_file_enforces_size_limit() -> None:
    backend = FakeBackend(size=SANDBOX_DOWNLOAD_MAX_BYTES + 1)

    with pytest.raises(SandboxDownloadFileTooLarge):
        await inspect_sandbox_file(backend, "/workspace/artifact.bin")


async def test_download_sandbox_file_with_local_backend(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"binary\x00content")
    backend = LocalShellBackend(root_dir=str(tmp_path), virtual_mode=True)

    info, content = await download_sandbox_file(backend, "/artifact.bin")

    assert info.size == len(content)
    assert content == b"binary\x00content"


async def test_download_sandbox_file_reads_nested_provider_content() -> None:
    backend = FakeBackend(content={"file_data": {"content": "abc"}})

    info, content = await download_sandbox_file(backend, "/workspace/artifact.bin")

    assert info.filename == "artifact.bin"
    assert info.size == 3
    assert content == b"abc"
    assert backend.deleted_paths == [backend.staged_path]


async def test_download_sandbox_file_rejects_growth_during_staging() -> None:
    backend = FakeBackend(stage_error="too_large")

    with pytest.raises(SandboxDownloadFileTooLarge):
        await download_sandbox_file(backend, "/workspace/artifact.bin")

    assert backend.deleted_paths == [backend.staged_path]


async def test_download_sandbox_file_handles_staging_io_error() -> None:
    backend = FakeBackend(stage_error="io_error")

    with pytest.raises(SandboxDownloadError, match="could not be staged"):
        await download_sandbox_file(backend, "/workspace/artifact.bin")

    assert backend.deleted_paths == [backend.staged_path]


async def test_download_sandbox_file_handles_provider_error() -> None:
    backend = FakeBackend(content=SimpleNamespace(error="permission_denied", content=None))

    with pytest.raises(SandboxDownloadError, match="could not be downloaded"):
        await download_sandbox_file(backend, "/workspace/artifact.bin")

    assert backend.deleted_paths == [backend.staged_path]
