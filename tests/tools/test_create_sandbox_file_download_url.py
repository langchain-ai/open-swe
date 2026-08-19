import importlib
from types import SimpleNamespace
from typing import Any

import pytest

download_tool = importlib.import_module("agent.tools.create_sandbox_file_download_url")


class _Sandbox:
    def generate_download_url(self, path: str, **kwargs: Any) -> Any:
        raise AssertionError("sync download URL API must not be called")


class _AsyncClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False

    async def __aenter__(self) -> "_AsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.closed = True

    async def generate_download_url(self, name: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((name, path, kwargs))
        return SimpleNamespace(
            download_url="https://downloads.example/file?token=secret",
            token="secret",
            expires_at="2026-08-20T12:00:00Z",
        )


class _Backend:
    def __init__(self, sandbox: Any) -> None:
        self.id = "sandbox-1"
        self.sandbox = sandbox


def _configure(monkeypatch: pytest.MonkeyPatch, backend: _Backend) -> _AsyncClient:
    monkeypatch.setattr(
        download_tool,
        "get_config",
        lambda: {"configurable": {"thread_id": "thread-1"}},
    )

    async def get_backend(_thread_id: str) -> _Backend:
        return backend

    async def work_dir(_backend: _Backend) -> str:
        return "/workspace/project"

    client = _AsyncClient()
    monkeypatch.setattr(download_tool, "get_sandbox_backend", get_backend)
    monkeypatch.setattr(download_tool, "aresolve_sandbox_work_dir", work_dir)
    monkeypatch.setattr(download_tool, "unwrap_sandbox_backend", lambda value: value)
    monkeypatch.setattr(download_tool, "get_async_sandbox_client", lambda: client)
    return client


async def test_create_download_url_for_relative_path(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = _Sandbox()
    backend = _Backend(sandbox)
    client = _configure(monkeypatch, backend)

    result = await download_tool._create_sandbox_file_download_url(
        "artifacts/demo.mp4",
        expires_in_seconds=3600,
        content_type="video/mp4",
        content_disposition="inline",
    )

    assert result == {
        "url": "https://downloads.example/file?token=secret",
        "file_path": "/workspace/project/artifacts/demo.mp4",
        "expires_at": "2026-08-20T12:00:00Z",
    }
    assert "token" not in result
    assert client.closed is True
    assert client.calls == [
        (
            "sandbox-1",
            "/workspace/project/artifacts/demo.mp4",
            {
                "expires_in_seconds": 3600,
                "content_type": "video/mp4",
                "content_disposition": "inline",
            },
        )
    ]


async def test_create_download_url_uses_secure_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = _Sandbox()
    backend = _Backend(sandbox)
    client = _configure(monkeypatch, backend)

    await download_tool._create_sandbox_file_download_url("/tmp/result.zip")

    assert client.calls == [
        (
            "sandbox-1",
            "/tmp/result.zip",
            {
                "expires_in_seconds": 86400,
                "content_type": None,
                "content_disposition": "attachment",
            },
        )
    ]


async def test_create_download_url_rejects_unsupported_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _Backend(SimpleNamespace())
    _configure(monkeypatch, backend)

    with pytest.raises(RuntimeError, match="only available for LangSmith"):
        await download_tool._create_sandbox_file_download_url("report.pdf")


@pytest.mark.parametrize("expires_in_seconds", [0, -1])
async def test_create_download_url_rejects_invalid_expiry(
    monkeypatch: pytest.MonkeyPatch,
    expires_in_seconds: int,
) -> None:
    backend = _Backend(_Sandbox())
    _configure(monkeypatch, backend)

    with pytest.raises(ValueError, match="must be positive"):
        await download_tool._create_sandbox_file_download_url(
            "result.bin", expires_in_seconds=expires_in_seconds
        )
