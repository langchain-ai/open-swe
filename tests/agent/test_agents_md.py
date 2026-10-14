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
    ("blobs", "expected"),
    [
        ({"f0": {"text": "agents"}, "f1": {"text": "claude"}}, {"ui/AGENTS.md": "agents"}),
        ({"f0": None, "f1": {"text": "claude"}}, {"ui/CLAUDE.md": "claude"}),
    ],
)
async def test_fetch_scoped_agents_md_prefers_agents_md_then_claude_md_in_one_query(
    blobs: dict[str, object], expected: dict[str, str]
) -> None:
    graphql = AsyncMock(return_value={"repository": blobs})
    with patch("openswe.github.http.RepoClient.graphql", graphql):
        result = await agents_md.fetch_scoped_agents_md(
            "acme", "repo", "main", ["ui/app.py"], token="tok"
        )
    assert result == expected
    graphql.assert_awaited_once()
