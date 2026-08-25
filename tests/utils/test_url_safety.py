from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.utils import url_safety


@pytest.mark.asyncio
async def test_request_with_safe_redirects_applies_custom_validator_to_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = [(None, None, None, None, ("1.1.1.1", 0))]
    monkeypatch.setattr(
        url_safety,
        "resolve_and_validate",
        lambda url: (True, "", "allowed.example", resolved),
    )
    redirect = MagicMock(status_code=302, headers={"Location": "https://blocked.example/file"})
    client = MagicMock()
    client.request = AsyncMock(return_value=redirect)

    response, blocked = await url_safety.request_with_safe_redirects(
        client,
        "GET",
        "https://allowed.example/file",
        validate_url=lambda url: (
            (True, "") if url.startswith("https://allowed.example/") else (False, "blocked host")
        ),
    )

    assert response is None
    assert blocked is not None
    assert blocked["content"] == "Request blocked: blocked host"
    client.request.assert_awaited_once()
