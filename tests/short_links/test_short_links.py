"""Opening short links, against a real migrated schema."""

from datetime import UTC, datetime, timedelta

import httpx

from openswe.short_links.models import LINKS_PATH, ShortLink

TARGET = "https://downloads.example/file?token=secret"


async def _open(token: str) -> httpx.Response:
    from openswe.api.app import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://openswe.example"
    ) as http:
        return await http.get(f"{LINKS_PATH}/{token}")


async def test_opening_a_link_redirects_to_its_target(registry_db: None) -> None:
    link = await ShortLink.create(TARGET)

    response = await _open(link.token)

    assert response.status_code == 302
    assert response.headers["location"] == TARGET
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "set-cookie" not in response.headers


async def test_an_unknown_link_is_not_found(registry_db: None) -> None:
    response = await _open("no-such-link")

    assert response.status_code == 404


async def test_an_expired_link_is_gone(registry_db: None) -> None:
    link = await ShortLink.create(TARGET, expires_at=datetime.now(UTC) - timedelta(seconds=1))

    response = await _open(link.token)

    assert response.status_code == 410
    assert "location" not in response.headers
