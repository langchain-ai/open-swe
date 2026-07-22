from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from tenki_sandbox import AsyncClient, AsyncSandbox

from agent.integrations import tenki


@dataclass
class _CommandResult:
    stdout_text: str = ""
    stderr_text: str = ""
    exit_code: int = 0


class _FakeFS:
    def __init__(self) -> None:
        self.mkdir = AsyncMock()
        self.write_bytes = AsyncMock()
        self.stat = AsyncMock(return_value=SimpleNamespace(is_dir=False))
        self.read_bytes = AsyncMock(return_value=b"contents")


class _FakeSandbox:
    def __init__(self, sandbox_id: str = "sb-created", state: str = "RUNNING") -> None:
        self.id = sandbox_id
        self.state = state
        self.fs = _FakeFS()
        self.shell = AsyncMock(return_value=_CommandResult(stdout_text="out", stderr_text="err"))
        self.close_if_open = AsyncMock()
        self.resume = AsyncMock(side_effect=self._resume)
        self.wait_ready = AsyncMock(side_effect=self._ready)

    def _resume(self) -> None:
        self.state = "RESUMING"

    def _ready(self, _timeout: int) -> None:
        self.state = "RUNNING"


class _FakeClient:
    def __init__(self, sandbox: _FakeSandbox) -> None:
        self.sandbox = sandbox
        self.create = AsyncMock(return_value=sandbox)
        self.get = AsyncMock(return_value=sandbox)
        self.close = AsyncMock()


def test_validate_tenki_startup_config_requires_credentials(monkeypatch) -> None:
    monkeypatch.delenv("TENKI_API_KEY", raising=False)
    monkeypatch.delenv("TENKI_AUTH_TOKEN", raising=False)

    with pytest.raises(ValueError, match="TENKI_API_KEY or TENKI_AUTH_TOKEN"):
        tenki.validate_tenki_startup_config()


def test_validate_tenki_startup_config_requires_project(monkeypatch) -> None:
    monkeypatch.setenv("TENKI_API_KEY", "tenki-key")
    monkeypatch.delenv("TENKI_SANDBOX_PROJECT_ID", raising=False)

    with pytest.raises(ValueError, match="TENKI_SANDBOX_PROJECT_ID"):
        tenki.validate_tenki_startup_config()


async def test_create_tenki_sandbox_uses_native_async_client(monkeypatch) -> None:
    monkeypatch.setenv("TENKI_API_KEY", "tenki-key")
    monkeypatch.setenv("TENKI_SANDBOX_PROJECT_ID", "project-1")
    monkeypatch.setenv("TENKI_SANDBOX_IMAGE", "sandbox-v2")
    sandbox = _FakeSandbox()
    client = _FakeClient(sandbox)
    monkeypatch.setattr(tenki, "AsyncClient", lambda: client)

    backend = await tenki.create_tenki_sandbox()

    assert backend.id == "sb-created"
    client.create.assert_awaited_once_with(
        timeout=180,
        project_id="project-1",
        image="sandbox-v2",
    )


async def test_create_tenki_sandbox_resumes_on_reconnect(monkeypatch) -> None:
    monkeypatch.setenv("TENKI_API_KEY", "tenki-key")
    monkeypatch.setenv("TENKI_SANDBOX_PROJECT_ID", "project-1")
    sandbox = _FakeSandbox("sb-existing", state="PAUSED")
    client = _FakeClient(sandbox)
    monkeypatch.setattr(tenki, "AsyncClient", lambda: client)

    backend = await tenki.create_tenki_sandbox("sb-existing")

    assert backend.id == "sb-existing"
    sandbox.resume.assert_awaited_once()
    sandbox.wait_ready.assert_awaited_once_with(180)


async def test_tenki_backend_exec_and_github_auth_translation() -> None:
    sandbox = _FakeSandbox()
    client = _FakeClient(sandbox)
    backend = tenki.TenkiSandbox(
        client=cast(AsyncClient, client),
        sandbox=cast(AsyncSandbox, sandbox),
    )
    backend.set_github_token("github-token")

    result = await backend.aexecute("GH_TOKEN=dummy gh repo view")

    assert result.output == "out\nerr"
    sandbox.shell.assert_awaited_once_with(
        'GH_TOKEN="${GH_TOKEN:-dummy}" gh repo view',
        env={"GH_TOKEN": "github-token", "GIT_TOKEN": "github-token"},
        timeout=1800,
    )


async def test_tenki_backend_file_roundtrip_and_cleanup() -> None:
    sandbox = _FakeSandbox()
    client = _FakeClient(sandbox)
    backend = tenki.TenkiSandbox(
        client=cast(AsyncClient, client),
        sandbox=cast(AsyncSandbox, sandbox),
    )

    uploads = await backend.aupload_files([("/home/tenki/project/file.txt", b"contents")])
    downloads = await backend.adownload_files(["/home/tenki/project/file.txt"])
    await backend.aclose()

    assert uploads[0].error is None
    assert downloads[0].content == b"contents"
    sandbox.fs.mkdir.assert_awaited_once_with("/home/tenki/project", recursive=True)
    sandbox.close_if_open.assert_awaited_once()
    client.close.assert_awaited_once()
