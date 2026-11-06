"""Opening short links, against a real migrated schema."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from openswe.database import postgres
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


@pytest.mark.parametrize(
    "target",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "//evil.example/path",
        "http://downloads.example/file",
        "https:///no-host",
        "/dashboard/relative",
        "JavaScript:alert(1)",
        "jav%61script:alert(1)",
        " https://downloads.example/file",
        "https://downloads.example/file\n",
        "https://user:pass@evil.example/",
        "https://downloads.example@evil.example/",
        "https://[::1/",
    ],
)
async def test_a_link_must_target_an_absolute_https_url(target: str) -> None:
    with pytest.raises(ValueError, match="absolute https URL"):
        await ShortLink.create(target)


async def test_a_stored_link_with_an_unsafe_target_is_not_followed(registry_db: None) -> None:
    link = ShortLink(token="stored-unsafe", target_url="javascript:alert(1)", expires_at=None)
    async with postgres.session() as session:
        session.add(link)

    response = await _open(link.token)

    assert response.status_code == 404
    assert "location" not in response.headers
