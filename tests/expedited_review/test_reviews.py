"""GitHub review submission retries an invalid voter token after refresh."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import pytest

from agent.expedited_review import reviews


@pytest.mark.parametrize("refreshed_status", [200, 401])
async def test_review_retries_401_with_refreshed_voter_token(
    monkeypatch: pytest.MonkeyPatch, refreshed_status: int
) -> None:
    tokens = AsyncMock(side_effect=["stale", "fresh"])
    monkeypatch.setattr(reviews, "get_valid_access_token", tokens)
    monkeypatch.setattr(reviews, "_review_body", AsyncMock(return_value="Approved"))
    used_tokens: list[str] = []

    class Client:
        def __init__(self, token: str) -> None:
            used_tokens.append(token)

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    async def request(client: Client, method: str, url: str, **kwargs: object) -> httpx2.Response:
        status = 401 if used_tokens[-1] == "stale" else refreshed_status
        return httpx2.Response(
            status,
            json={"id": 42} if status == 200 else {"message": "Bad credentials"},
            request=httpx2.Request(method, url),
        )

    monkeypatch.setattr(reviews, "github_client", lambda *, token: Client(token))
    monkeypatch.setattr(reviews, "github_request", request)
    vote = SimpleNamespace(github_login="reviewer", github_review_id=None, github_review_sha="")
    approval = SimpleNamespace(pull_request=SimpleNamespace(owner="org", repo="repo", number=1))

    result = await reviews.submit_approval(approval, vote, "head")

    assert used_tokens == ["stale", "fresh"]
    tokens.assert_any_await("reviewer", force_refresh=True)
    if refreshed_status == 200:
        assert result is None
        assert (vote.github_review_id, vote.github_review_sha) == (42, "head")
    else:
        assert result is not None and "Sign in again" in result
        assert vote.github_review_id is None
