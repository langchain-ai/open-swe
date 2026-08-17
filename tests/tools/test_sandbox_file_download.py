from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

from agent.tools.create_sandbox_file_download import create_sandbox_file_download
from agent.utils.sandbox_downloads import SandboxDownloadLink, SandboxFileInfo


async def test_create_sandbox_file_download_returns_link() -> None:
    config = {"configurable": {"thread_id": "thread-1"}}
    info = SandboxFileInfo(
        path="/workspace/artifact.zip",
        filename="artifact.zip",
        size=42,
    )

    with (
        patch("agent.tools.create_sandbox_file_download.get_config", return_value=config),
        patch(
            "agent.tools.create_sandbox_file_download.get_sandbox_backend",
            new_callable=AsyncMock,
            return_value=object(),
        ),
        patch(
            "agent.tools.create_sandbox_file_download.inspect_sandbox_file",
            new_callable=AsyncMock,
            return_value=info,
        ),
        patch(
            "agent.tools.create_sandbox_file_download.create_sandbox_download_link",
            return_value=SandboxDownloadLink(
                url="https://example.test/download",
                expires_at=datetime.fromisoformat("2026-08-18T12:00:00+00:00"),
            ),
        ),
    ):
        result = await create_sandbox_file_download("/workspace/artifact.zip")

    assert result == {
        "success": True,
        "url": "https://example.test/download",
        "file_path": "/workspace/artifact.zip",
        "filename": "artifact.zip",
        "size_bytes": 42,
        "expires_at": "2026-08-18T12:00:00+00:00",
    }


async def test_create_sandbox_file_download_requires_thread() -> None:
    with patch(
        "agent.tools.create_sandbox_file_download.get_config",
        return_value={"configurable": {}},
    ):
        result = await create_sandbox_file_download("/workspace/artifact.zip")

    assert result == {"success": False, "error": "no thread_id in run config"}


def test_create_sandbox_file_download_exported() -> None:
    from agent.tools import create_sandbox_file_download as exported

    assert exported is create_sandbox_file_download
