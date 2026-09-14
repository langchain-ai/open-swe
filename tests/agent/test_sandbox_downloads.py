from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from agent.api import sandbox_downloads


async def test_sandbox_download_redirects_to_stored_url(monkeypatch) -> None:
    async def get_value(_namespace, _handle):
        return {
            "url": "https://downloads.example/file?token=secret",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }

    monkeypatch.setattr(sandbox_downloads, "get_value", get_value)

    response = await sandbox_downloads.sandbox_download("short-handle")

    assert response.status_code == 307
    assert response.headers["location"] == "https://downloads.example/file?token=secret"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "record",
    [
        None,
        {"url": "https://downloads.example/file", "expires_at": "invalid"},
        {"url": "http://downloads.example/file", "expires_at": None},
    ],
)
async def test_sandbox_download_rejects_missing_expired_or_unsafe_records(
    monkeypatch, record
) -> None:
    async def get_value(_namespace, _handle):
        return record

    monkeypatch.setattr(sandbox_downloads, "get_value", get_value)

    with pytest.raises(HTTPException) as exc_info:
        await sandbox_downloads.sandbox_download("short-handle")

    assert exc_info.value.status_code == 404
