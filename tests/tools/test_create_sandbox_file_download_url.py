import importlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from openswe.short_links.models import ShortLink
from openswe.users import User

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


def _configure(
    monkeypatch: pytest.MonkeyPatch, backend: _Backend, **configurable: str
) -> _AsyncClient:
    config = {"configurable": {"thread_id": "thread-1", **configurable}}
    monkeypatch.setattr("openswe.run_config.get_config", lambda: config)
    monkeypatch.setattr(download_tool, "get_config", lambda: config)

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


SIGNED_URL = "https://downloads.example/file?token=secret"


async def test_a_public_deployment_hands_out_a_short_link_to_the_signed_url(
    monkeypatch: pytest.MonkeyPatch, registry_db: None
) -> None:
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://openswe.example")
    _configure(monkeypatch, _Backend())

    result = await download_tool.create_sandbox_file_download_url("shot.png")

    prefix = "https://openswe.example/dashboard/api/l/"
    assert result["url"].startswith(prefix)
    assert len(result["url"]) < 100
    assert "token" not in result["url"]
    assert result["file_path"] == "/workspace/project/shot.png"
    assert result["expires_at"] == "2026-08-20T12:00:00Z"
    link = await ShortLink.by_token(result["url"].removeprefix(prefix))
    assert link is not None
    assert link.target_url == SIGNED_URL
    assert link.expires_at == datetime(2026, 8, 20, 12, tzinfo=UTC)


@pytest.mark.parametrize(("run", "attributed"), [({}, True), ({"schedule_id": "daily"}, False)])
async def test_a_short_link_records_the_person_who_started_the_run(
    monkeypatch: pytest.MonkeyPatch, registry_db: None, run: dict[str, str], attributed: bool
) -> None:
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://openswe.example")
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada")
    ada = await User.sign_in("github", "1", login="ada")
    _configure(monkeypatch, _Backend(), source="dashboard", github_login="ada", **run)

    result = await download_tool.create_sandbox_file_download_url("shot.png")

    link = await ShortLink.by_token(result["url"].rsplit("/", 1)[1])
    assert link is not None
    person = ada.id if attributed else None
    assert (link.created_by, link.updated_by) == (person, person)


async def test_local_development_falls_back_to_the_signed_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "http://localhost:2024")
    _configure(monkeypatch, _Backend())

    result = await download_tool.create_sandbox_file_download_url("result.zip")

    assert result == {
        "url": SIGNED_URL,
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
