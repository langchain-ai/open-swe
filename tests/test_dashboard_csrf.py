"""CSRF defenses for cookie-authenticated dashboard mutations."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from agent.dashboard import oauth, thread_api


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
async def test_require_same_origin_accepts_referer(monkeypatch) -> None:
    monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dashboard.example")

    oauth.require_same_origin(_request(referer="https://dashboard.example/agents/thread-id"))


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


async def test_proxy_commands_rejects_non_json_content_type() -> None:
    with pytest.raises(HTTPException) as exc:
        await thread_api.proxy_dashboard_thread_commands(
            "tid",
            "octocat",
            b'{"method": "run.start"}',
            content_type="text/plain",
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
