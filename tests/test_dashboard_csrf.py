"""CSRF defenses for cookie-authenticated dashboard mutations."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from agent.dashboard import oauth, routes, thread_api


def _request(
    *,
    method: str = "POST",
    path: str = "/dashboard/api/threads/tid/commands",
    origin: str | None = None,
    referer: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if referer is not None:
        headers.append((b"referer", referer.encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers,
    }
    return Request(scope)


@pytest.fixture
def dashboard_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://testserver")
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


@pytest.mark.asyncio
async def test_require_same_origin_noop_when_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
    monkeypatch.delenv("DASHBOARD_ALLOWED_ORIGINS", raising=False)

    oauth.require_same_origin(_request(origin="https://evil.example"))


@pytest.mark.asyncio
async def test_require_same_origin_allows_configured_origin(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "https://preview.example")

    oauth.require_same_origin(_request(origin="https://dashboard.example"))
    oauth.require_same_origin(_request(origin="https://preview.example"))


@pytest.mark.asyncio
async def test_require_same_origin_normalizes_case_and_trailing_slash(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "HTTPS://Dashboard.Example/")
    monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "https://Preview.Example/")

    oauth.require_same_origin(_request(origin="https://dashboard.example"))
    oauth.require_same_origin(_request(origin="https://preview.example"))


@pytest.mark.asyncio
async def test_require_same_origin_accepts_referer(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")

    oauth.require_same_origin(_request(referer="https://dashboard.example/agents/thread-id"))


@pytest.mark.asyncio
async def test_require_same_origin_rejects_missing_origin_and_referer(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")

    with pytest.raises(HTTPException) as exc:
        oauth.require_same_origin(_request())

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_same_origin_rejects_null_origin(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")

    with pytest.raises(HTTPException) as exc:
        oauth.require_same_origin(_request(origin="null"))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_same_origin_rejects_unknown_origin(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")

    with pytest.raises(HTTPException) as exc:
        oauth.require_same_origin(_request(origin="https://evil.example"))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_same_origin_rejects_prefix_bypass(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")

    with pytest.raises(HTTPException) as exc:
        oauth.require_same_origin(_request(origin="https://dashboard.example.evil"))

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_same_origin_does_not_fallback_when_origin_invalid(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")

    with pytest.raises(HTTPException) as exc:
        oauth.require_same_origin(
            _request(origin="null", referer="https://dashboard.example/agents")
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_same_origin_for_mutations_skips_get(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")

    oauth.require_same_origin_for_mutations(
        _request(method="GET", path="/dashboard/api/me", origin="https://evil.example")
    )


@pytest.mark.asyncio
async def test_require_same_origin_for_mutations_enforces_post(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")

    with pytest.raises(HTTPException) as exc:
        oauth.require_same_origin_for_mutations(_request(origin="https://evil.example"))

    assert exc.value.status_code == 403


def test_router_rejects_cross_site_text_plain_post(dashboard_client: TestClient) -> None:
    response = dashboard_client.post(
        "/dashboard/api/auth/logout",
        headers={"Origin": "https://evil.example", "Content-Type": "text/plain"},
        content='{"method": "run.start"}',
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "CSRF check failed"


def test_router_rejects_post_without_origin(dashboard_client: TestClient) -> None:
    response = dashboard_client.post(
        "/dashboard/api/auth/logout",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 403


def test_router_allows_allowed_origin_post(dashboard_client: TestClient) -> None:
    response = dashboard_client.post(
        "/dashboard/api/auth/logout",
        headers={"Origin": "http://testserver", "Content-Type": "application/json"},
    )

    assert response.status_code == 204


async def test_proxy_commands_rejects_non_json_content_type() -> None:
    for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
        with pytest.raises(HTTPException) as exc:
            await thread_api.proxy_dashboard_thread_commands(
                "tid",
                "octocat",
                b'{"method": "run.start"}',
                content_type=content_type,
            )

        assert exc.value.status_code == 415


async def test_proxy_commands_accepts_json_content_type_with_charset() -> None:
    with pytest.raises(HTTPException) as exc:
        await thread_api.proxy_dashboard_thread_commands(
            "tid",
            "octocat",
            b"not-json",
            content_type="application/json; charset=utf-8",
        )

    assert exc.value.status_code == 400
