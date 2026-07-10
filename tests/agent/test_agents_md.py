from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent.utils import agents_md


def _make_response(status: int, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


@pytest.mark.asyncio
async def test_fetch_agents_md_returns_content() -> None:
    with patch("httpx.AsyncClient") as mock_client_cls:
        client = MagicMock()
        client.get = AsyncMock(return_value=_make_response(200, "# AGENTS.md\nrules"))
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await agents_md.fetch_agents_md("acme", "repo", "main", token="tok")
    assert result == "# AGENTS.md\nrules"


@pytest.mark.asyncio
async def test_fetch_agents_md_falls_back_to_claude_md() -> None:
    with patch("httpx.AsyncClient") as mock_client_cls:
        client = MagicMock()
        client.get = AsyncMock(
            side_effect=[
                _make_response(404),
                _make_response(200, "# CLAUDE.md\nrules"),
            ]
        )
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await agents_md.fetch_agents_md("acme", "repo", "main", token="tok")
    assert result == "# CLAUDE.md\nrules"
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_fetch_agents_md_returns_none_when_both_missing() -> None:
    with patch("httpx.AsyncClient") as mock_client_cls:
        client = MagicMock()
        client.get = AsyncMock(
            side_effect=[
                _make_response(404),
                _make_response(404),
            ]
        )
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await agents_md.fetch_agents_md("acme", "repo", "main", token="tok")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_agents_md_skips_oversized_file() -> None:
    big = "x" * (agents_md._MAX_AGENTS_MD_BYTES + 1)
    with patch("httpx.AsyncClient") as mock_client_cls:
        client = MagicMock()
        client.get = AsyncMock(return_value=_make_response(200, big))
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await agents_md.fetch_agents_md("acme", "repo", "main", token="tok")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_agents_md_oversized_agents_md_does_not_fall_back_to_claude_md() -> None:
    big = "x" * (agents_md._MAX_AGENTS_MD_BYTES + 1)
    with patch("httpx.AsyncClient") as mock_client_cls:
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
async def test_fetch_agents_md_handles_http_error() -> None:
    with patch("httpx.AsyncClient") as mock_client_cls:
        client = MagicMock()
        client.get = AsyncMock(side_effect=httpx.HTTPError("boom"))
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        result = await agents_md.fetch_agents_md("acme", "repo", "main", token="tok")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_agents_md_returns_none_for_missing_params() -> None:
    result = await agents_md.fetch_agents_md("", "repo", "main", token="tok")
    assert result is None
    result = await agents_md.fetch_agents_md("acme", "", "main", token="tok")
    assert result is None
    result = await agents_md.fetch_agents_md("acme", "repo", "", token="tok")
    assert result is None
