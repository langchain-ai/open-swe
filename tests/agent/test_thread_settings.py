from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.utils import ttl_cache
from agent.utils.thread_settings import (
    THREAD_SETTINGS_KEY,
    load_thread_settings,
    store_thread_settings,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    ttl_cache.clear()
    yield
    ttl_cache.clear()


def _client(metadata: dict[str, Any]) -> MagicMock:
    client = MagicMock()
    client.threads.get = AsyncMock(return_value={"metadata": metadata})
    client.threads.update = AsyncMock()
    return client


class TestLoadThreadSettings:
    async def test_returns_stored_settings(self) -> None:
        client = _client({THREAD_SETTINGS_KEY: {"model_id": "openai:gpt-5.6-sol"}})

        assert (await load_thread_settings(client, "t1")).get("model_id") == "openai:gpt-5.6-sol"

    async def test_falls_back_to_thread_owner_when_unset(self) -> None:
        client = _client({"github_login": "ramon-langchain"})

        settings = await load_thread_settings(client, "t1")

        assert settings == {"owner_login": "ramon-langchain"}

    async def test_returns_empty_when_thread_unreadable(self) -> None:
        client = MagicMock()
        client.threads.get = AsyncMock(side_effect=RuntimeError("gone"))

        assert await load_thread_settings(client, "t1") == {}


class TestStoreThreadSettings:
    async def test_persists_and_serves_from_cache(self) -> None:
        client = _client({})

        await store_thread_settings(client, "t1", {"model_id": "openai:gpt-5.6-sol"})

        client.threads.update.assert_awaited_once_with(
            thread_id="t1", metadata={THREAD_SETTINGS_KEY: {"model_id": "openai:gpt-5.6-sol"}}
        )
        # Subsequent reads come from the cache rather than the thread.
        assert (await load_thread_settings(client, "t1")).get("model_id") == "openai:gpt-5.6-sol"
        client.threads.get.assert_not_awaited()
