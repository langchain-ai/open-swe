from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.github.token_scope import GITHUB_TOKEN_REPOSITORIES_KEY
from agent.sandboxes import state


@pytest.fixture(autouse=True)
def _empty_cache() -> None:
    state.clear_thread_token_repositories()


def _client(monkeypatch: pytest.MonkeyPatch, get: AsyncMock) -> None:
    client = MagicMock()
    client.threads.get = get
    monkeypatch.setattr(state, "get_client", lambda: client)


async def test_the_scope_is_read_from_the_live_thread_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = AsyncMock(return_value={"metadata": {GITHUB_TOKEN_REPOSITORIES_KEY: ["acme/oss"]}})
    _client(monkeypatch, get)

    assert await state.thread_token_repositories("thread") == ["acme/oss"]
    assert await state.thread_token_repositories("thread") == ["acme/oss"]
    get.assert_awaited_once_with("thread")


async def test_a_thread_without_a_scope_gets_the_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _client(monkeypatch, AsyncMock(return_value={"metadata": {"source": "slack"}}))

    assert await state.thread_token_repositories("thread") is None


async def test_a_failed_read_propagates_and_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get = AsyncMock(
        side_effect=[
            RuntimeError("down"),
            {"metadata": {GITHUB_TOKEN_REPOSITORIES_KEY: ["acme/oss"]}},
        ]
    )
    _client(monkeypatch, get)

    with pytest.raises(RuntimeError):
        await state.thread_token_repositories("thread")
    assert await state.thread_token_repositories("thread") == ["acme/oss"]
