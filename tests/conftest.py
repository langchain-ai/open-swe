"""Shared pytest fixtures."""

from collections.abc import Iterator, Sequence
from typing import Any

import httpx
import pytest

from agent import store as agent_store
from agent.utils import ttl_cache
from agent.webhooks import common as webhook_common


class FakeStore:
    """In-memory stand-in for the LangGraph Store, in the SDK's item shape.

    Backs the real ``agent.store`` code path, so values round-trip through
    ``model_dump``/``model_validate`` the way they do in production.
    """

    def __init__(self) -> None:
        self.items: dict[tuple[str, ...], dict[str, dict[str, Any]]] = {}

    def seed(self, namespace: Sequence[str], key: str, value: dict[str, Any]) -> None:
        self.items.setdefault(tuple(namespace), {})[key] = dict(value)

    def values(self, namespace: Sequence[str]) -> dict[str, dict[str, Any]]:
        return self.items.get(tuple(namespace), {})

    async def get_item(self, namespace: Sequence[str], key: str) -> dict[str, Any]:
        value = self.values(namespace).get(key)
        if value is None:
            raise httpx.HTTPStatusError(
                "not found",
                request=httpx.Request("GET", "http://test"),
                response=httpx.Response(404),
            )
        return {"value": dict(value)}

    async def put_item(self, namespace: Sequence[str], key: str, value: dict[str, Any]) -> None:
        self.seed(namespace, key, value)

    async def delete_item(self, namespace: Sequence[str], key: str) -> None:
        self.values(namespace).pop(key, None)

    async def search_items(
        self,
        namespace: Sequence[str],
        *,
        filter: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        matches = [
            {"value": dict(value)}
            for value in self.values(namespace).values()
            if all(value.get(k) == expected for k, expected in (filter or {}).items())
        ]
        return {"items": matches[offset : offset + limit]}


class FakeStoreClient:
    def __init__(self) -> None:
        self.store = FakeStore()


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """Route every ``agent.store`` access to an in-memory store for this test."""
    client = FakeStoreClient()
    monkeypatch.setattr(agent_store, "store_client", lambda: client)
    return client.store


@pytest.fixture(autouse=True)
def _reset_ttl_cache() -> Iterator[None]:
    """Keep the process-global TTL cache from leaking team settings between tests."""
    ttl_cache.clear()
    yield
    ttl_cache.clear()


@pytest.fixture(autouse=True)
def _default_enable_auto_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat automatic reviews as enabled for every repo by default.

    The dashboard's opt-in list (loaded by :func:`agent.dashboard.enabled_repos.is_review_repo_enabled`)
    is empty in the test environment because there is no live LangGraph Store.

    Tests targeting the automatic-review gate should override this fixture or set
    ``monkeypatch.setattr(webhook_common, "is_review_repo_enabled", ...)`` to a stricter stub.
    """

    async def _enabled(_owner: str, _name: str) -> bool:
        return True

    monkeypatch.setattr(webhook_common, "is_review_repo_enabled", _enabled)
