from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.api.app import create_app
from agent.dashboard import sandbox_download_api
from agent.utils.sandbox_downloads import SandboxFileInfo, create_sandbox_download_link


async def test_download_route_requires_owner_and_returns_attachment(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-that-is-at-least-32-bytes")
    token = create_sandbox_download_link("thread-1", "/workspace/report.csv").url.rsplit("/", 1)[-1]
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
        "download_sandbox_file",
        AsyncMock(
            return_value=(
                SandboxFileInfo(
                    path="/workspace/report.csv",
                    filename="report.csv",
                    size=5,
                ),
                b"a,b\n1",
            )
        ),
    )

    response = await sandbox_download_api.download_sandbox_file_route(
        token,
        {"sub": "octocat", "email": "octocat@example.com"},
    )

    authorize.assert_awaited_once_with(
        "thread-1",
        "octocat",
        email="octocat@example.com",
    )
    assert response.body == b"a,b\n1"
    assert response.media_type == "text/csv"
    assert response.headers["content-disposition"].startswith('attachment; filename="report.csv"')
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


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
