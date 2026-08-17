from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from agent.api.app import create_app
from agent.dashboard import sandbox_download_api
from agent.utils.sandbox_downloads import (
    SandboxDownloadError,
    SandboxFileInfo,
    create_sandbox_download_link,
)


async def report_chunks(_backend, _info):
    yield b"a,b\n1"


async def empty_chunks(_backend, _info):
    if False:
        yield b""


async def response_body(response: StreamingResponse) -> bytes:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


async def test_download_route_requires_owner_and_streams_attachment(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-that-is-at-least-32-bytes")
    token = create_sandbox_download_link(
        "thread-1", "sandbox-1", "/workspace/report.csv"
    ).url.rsplit("/", 1)[-1]
    authorize = AsyncMock(return_value=("sandbox-1", "repo"))
    backend = object()
    monkeypatch.setattr(sandbox_download_api, "get_dashboard_terminal_sandbox", authorize)
    monkeypatch.setattr(
        sandbox_download_api,
        "create_sandbox",
        AsyncMock(return_value=backend),
    )
    monkeypatch.setattr(
        sandbox_download_api,
        "inspect_sandbox_file",
        AsyncMock(
            return_value=SandboxFileInfo(
                path="/workspace/report.csv",
                filename="report.csv",
                size=5,
                signature="signature",
            )
        ),
    )
    monkeypatch.setattr(sandbox_download_api, "stream_sandbox_file_chunks", report_chunks)

    response = await sandbox_download_api.download_sandbox_file_route(
        token,
        {"sub": "octocat", "email": "octocat@example.com"},
    )
    body = await response_body(response)

    assert authorize.await_count == 3
    authorize.assert_awaited_with(
        "thread-1",
        "octocat",
        email="octocat@example.com",
    )
    assert body == b"a,b\n1"
    assert response.media_type == "text/csv"
    assert response.headers["content-length"] == "5"
    assert response.headers["content-disposition"].startswith('attachment; filename="report.csv"')
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_download_route_rejects_replaced_sandbox(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-that-is-at-least-32-bytes")
    token = create_sandbox_download_link(
        "thread-1", "sandbox-old", "/workspace/report.csv"
    ).url.rsplit("/", 1)[-1]
    monkeypatch.setattr(
        sandbox_download_api,
        "get_dashboard_terminal_sandbox",
        AsyncMock(return_value=("sandbox-new", "repo")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await sandbox_download_api.download_sandbox_file_route(
            token,
            {"sub": "octocat"},
        )

    assert exc_info.value.status_code == 404
    assert "no longer available" in exc_info.value.detail


async def test_download_route_rejects_recreation_during_inspection(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-that-is-at-least-32-bytes")
    token = create_sandbox_download_link(
        "thread-1", "sandbox-old", "/workspace/report.csv"
    ).url.rsplit("/", 1)[-1]
    monkeypatch.setattr(
        sandbox_download_api,
        "get_dashboard_terminal_sandbox",
        AsyncMock(side_effect=[("sandbox-old", "repo"), ("sandbox-new", "repo")]),
    )
    monkeypatch.setattr(
        sandbox_download_api,
        "create_sandbox",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        sandbox_download_api,
        "inspect_sandbox_file",
        AsyncMock(
            return_value=SandboxFileInfo(
                path="/workspace/report.csv",
                filename="report.csv",
                size=5,
                signature="signature",
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await sandbox_download_api.download_sandbox_file_route(
            token,
            {"sub": "octocat"},
        )

    assert exc_info.value.status_code == 404
    assert "no longer available" in exc_info.value.detail


async def test_download_route_stops_after_recreation_during_stream(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-that-is-at-least-32-bytes")
    token = create_sandbox_download_link(
        "thread-1", "sandbox-old", "/workspace/report.csv"
    ).url.rsplit("/", 1)[-1]
    monkeypatch.setattr(
        sandbox_download_api,
        "get_dashboard_terminal_sandbox",
        AsyncMock(
            side_effect=[
                ("sandbox-old", "repo"),
                ("sandbox-old", "repo"),
                ("sandbox-new", "repo"),
            ]
        ),
    )
    monkeypatch.setattr(
        sandbox_download_api,
        "create_sandbox",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        sandbox_download_api,
        "inspect_sandbox_file",
        AsyncMock(
            return_value=SandboxFileInfo(
                path="/workspace/report.csv",
                filename="report.csv",
                size=5,
                signature="signature",
            )
        ),
    )
    monkeypatch.setattr(sandbox_download_api, "stream_sandbox_file_chunks", report_chunks)

    response = await sandbox_download_api.download_sandbox_file_route(
        token,
        {"sub": "octocat"},
    )

    with pytest.raises(SandboxDownloadError, match="no longer available"):
        await response_body(response)


async def test_empty_download_stops_after_recreation(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-that-is-at-least-32-bytes")
    token = create_sandbox_download_link(
        "thread-1", "sandbox-old", "/workspace/empty.bin"
    ).url.rsplit("/", 1)[-1]
    monkeypatch.setattr(
        sandbox_download_api,
        "get_dashboard_terminal_sandbox",
        AsyncMock(
            side_effect=[
                ("sandbox-old", "repo"),
                ("sandbox-old", "repo"),
                ("sandbox-new", "repo"),
            ]
        ),
    )
    monkeypatch.setattr(
        sandbox_download_api,
        "create_sandbox",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        sandbox_download_api,
        "inspect_sandbox_file",
        AsyncMock(
            return_value=SandboxFileInfo(
                path="/workspace/empty.bin",
                filename="empty.bin",
                size=0,
                signature="signature",
            )
        ),
    )
    monkeypatch.setattr(sandbox_download_api, "stream_sandbox_file_chunks", empty_chunks)

    response = await sandbox_download_api.download_sandbox_file_route(
        token,
        {"sub": "octocat"},
    )

    with pytest.raises(SandboxDownloadError, match="no longer available"):
        await response_body(response)


async def test_download_route_rejects_invalid_token(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-that-is-at-least-32-bytes")

    with pytest.raises(HTTPException) as exc_info:
        await sandbox_download_api.download_sandbox_file_route(
            "invalid",
            {"sub": "octocat"},
        )

    assert exc_info.value.status_code == 401


def test_sandbox_download_router_is_mounted() -> None:
    paths = create_app().openapi()["paths"]

    assert "/dashboard/api/sandbox-files/{token}" in paths
