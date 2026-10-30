import importlib
from types import SimpleNamespace
from typing import Any

import pytest

from openswe.sandboxes.file_links import get_link
from openswe.transcript.engine import Command, append
from openswe.transcript.events import ThreadCreated

download_tool = importlib.import_module("openswe.tools.create_sandbox_file_download_url")


class _AsyncClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False

    async def __aenter__(self) -> _AsyncClient:
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
    id = "sandbox-1"

    async def aexecute(self, command: str) -> Any:
        path = command.removeprefix("realpath -- ").strip("'")
        if path == "/workspace/project/link-to-secret":
            path = "/etc/passwd"
        return SimpleNamespace(exit_code=0, output=f"{path}\n")


def _configure(monkeypatch: pytest.MonkeyPatch, backend: _Backend) -> _AsyncClient:
    monkeypatch.setattr(
        "openswe.run_config.get_config", lambda: {"configurable": {"thread_id": "thread-1"}}
    )

    async def get_backend(_thread_id: str) -> _Backend:
        return backend

    async def work_dir(_backend: _Backend) -> str:
        return "/workspace/project"

    client = _AsyncClient()
    monkeypatch.setattr(download_tool, "get_sandbox_backend", get_backend)
    monkeypatch.setattr(download_tool, "resolve_sandbox_work_dir", work_dir)
    monkeypatch.setattr(download_tool, "unwrap_sandbox_backend", lambda value: value)
    monkeypatch.setattr(download_tool, "get_async_sandbox_client", lambda: client)
    return client


async def _create_thread(thread_id: str) -> None:
    created = ThreadCreated(title="A thread", source="dashboard", owner_login="test-user")
    await append(
        thread_id,
        [Command(command_id=f"thread:{thread_id}:created", event=created, actor_kind="user")],
    )


async def test_a_public_deployment_hands_out_a_short_link(
    monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://openswe.example")
    await _create_thread("thread-1")
    client = _configure(monkeypatch, _Backend())

    result = await download_tool.create_sandbox_file_download_url(
        "shot.png", content_type="image/png", content_disposition="inline"
    )

    prefix = "https://openswe.example/dashboard/api/l/"
    assert result["url"].startswith(prefix)
    assert len(result["url"]) < 100
    assert "token" not in result["url"]
    assert result["file_path"] == "/workspace/project/shot.png"
    assert result["expires_at"] is None
    assert client.calls == []
    link = await get_link(result["url"].removeprefix(prefix))
    assert link is not None
    assert (
        link.thread_id,
        link.sandbox_id,
        link.path,
        link.content_type,
        link.content_disposition,
    ) == ("thread-1", "sandbox-1", "/workspace/project/shot.png", "image/png", "inline")


async def test_a_short_link_reports_when_it_expires(
    monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://openswe.example")
    await _create_thread("thread-1")
    _configure(monkeypatch, _Backend())

    result = await download_tool.create_sandbox_file_download_url(
        "result.zip", expires_in_seconds=600
    )

    link = await get_link(result["url"].rsplit("/", 1)[1])
    assert link is not None and link.expires_at is not None
    assert result["expires_at"] == link.expires_at.isoformat()


async def test_a_thread_without_a_transcript_falls_back_to_the_signed_url(
    monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://openswe.example")
    _configure(monkeypatch, _Backend())

    result = await download_tool.create_sandbox_file_download_url("result.zip")

    assert result["url"] == "https://downloads.example/file?token=secret"


async def test_local_development_falls_back_to_the_signed_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "http://localhost:2024")
    _configure(monkeypatch, _Backend())

    result = await download_tool.create_sandbox_file_download_url("result.zip")

    assert result == {
        "url": "https://downloads.example/file?token=secret",
        "file_path": "/workspace/project/result.zip",
        "expires_at": "2026-08-20T12:00:00Z",
    }


async def test_create_download_url_defaults_to_a_non_expiring_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _Backend()
    client = _configure(monkeypatch, backend)

    await download_tool.create_sandbox_file_download_url("/workspace/project/result.zip")

    assert client.calls == [
        (
            "sandbox-1",
            "/workspace/project/result.zip",
            {
                "expires_in_seconds": None,
                "content_type": None,
                "content_disposition": "attachment",
            },
        )
    ]


@pytest.mark.parametrize(
    "file_path",
    ["../secret.txt", "artifacts/../../secret.txt", "/etc/passwd", "/workspace/project-other/x"],
)
async def test_create_download_url_rejects_paths_outside_work_dir(
    monkeypatch: pytest.MonkeyPatch,
    file_path: str,
) -> None:
    backend = _Backend()
    client = _configure(monkeypatch, backend)

    result = await download_tool.create_sandbox_file_download_url(file_path)

    assert "must resolve within the sandbox work directory" in result["error"]

    assert client.calls == []


async def test_create_download_url_rejects_symlink_outside_work_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _Backend()
    client = _configure(monkeypatch, backend)

    result = await download_tool.create_sandbox_file_download_url("link-to-secret")

    assert "must resolve within the sandbox work directory" in result["error"]

    assert client.calls == []
