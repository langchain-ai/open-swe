from unittest.mock import AsyncMock, MagicMock

import pytest

from openswe.utils import url_safety


@pytest.mark.asyncio
async def test_request_with_safe_redirects_applies_custom_validator_to_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        url_safety,
        "resolve_and_validate",
        lambda url: ("allowed.example", ["1.1.1.1"]),
    )
    redirect = MagicMock(status_code=302, headers={"Location": "https://blocked.example/file"})
    client = MagicMock()
    client.request = AsyncMock(return_value=redirect)

    def validate(url: str) -> None:
        if not url.startswith("https://allowed.example/"):
            raise url_safety.UnsafeUrlError(url, "blocked host")

    with pytest.raises(url_safety.UnsafeUrlError, match="Request blocked: blocked host"):
        await url_safety.request_with_safe_redirects(
            client, "GET", "https://allowed.example/file", validate_url=validate
        )
    client.request.assert_awaited_once()
