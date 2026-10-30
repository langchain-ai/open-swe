from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi import Response
from starlette.requests import Request

from openswe.sandboxes.browsers import BrowserWindow, LangSmithBrowser, ThreadBrowser, WindowLinks
from openswe.threads import browser


def _request(origin: str) -> Request:
    return Request({"type": "http", "headers": [(b"origin", origin.encode())]})


@pytest.mark.parametrize(
    ("origin", "embed_origins"),
    [
        ("https://dash.example.com", ["https://dash.example.com"]),
        ("open-swe://app", ["https://dash.example.com", "open-swe://app"]),
    ],
)
async def test_act_link_lets_the_requesting_page_frame_the_window(
    monkeypatch, origin, embed_origins
):
    monkeypatch.setattr(browser, "dashboard_base_url", lambda: "https://dash.example.com")
    links = AsyncMock(
        return_value=WindowLinks(
            view_url="https://ls/view", act_url="https://ls/act", expires_at=datetime.now(UTC)
        )
    )
    monkeypatch.setattr(LangSmithBrowser, "links", links)

    await browser._view(
        ThreadBrowser(browser="b", opened=True),
        BrowserWindow(id="w"),
        _request(origin),
        Response(),
    )

    links.assert_awaited_once_with("w", embed_origins=embed_origins)
