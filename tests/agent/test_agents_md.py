from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openswe.utils import agents_md


def _make_response(status: int, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


@pytest.mark.asyncio
async def test_fetch_agents_md_oversized_agents_md_does_not_fall_back_to_claude_md() -> None:
    big = "x" * (agents_md._MAX_AGENTS_MD_BYTES + 1)
    with patch("httpx2.AsyncClient") as mock_client_cls:
        client = MagicMock()
        client.get = AsyncMock(
            side_effect=[
                _make_response(200, big),
                _make_response(200, "# CLAUDE.md\nrules"),
            ]
        )
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await agents_md.fetch_agents_md("acme", "repo", "main", token="tok")
    assert result is None
    assert client.get.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("responses", "expected"),
    [
        ([_make_response(200, "# AGENTS.md\nrules")], {"ui/AGENTS.md": "# AGENTS.md\nrules"}),
        (
            [_make_response(404), _make_response(200, "# CLAUDE.md\nrules")],
            {"ui/CLAUDE.md": "# CLAUDE.md\nrules"},
        ),
    ],
)
async def test_fetch_scoped_agents_md_prefers_agents_md_then_claude_md(
    responses: list[MagicMock], expected: dict[str, str]
) -> None:
    with patch("httpx2.AsyncClient") as mock_client_cls:
        client = MagicMock()
        client.get = AsyncMock(side_effect=responses)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await agents_md.fetch_scoped_agents_md(
            "acme", "repo", "main", ["ui/app.py"], token="tok"
        )
    assert result == expected
    assert client.get.await_count == len(responses)
