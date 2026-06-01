from __future__ import annotations

from typing import Any

import pytest

from agent.dashboard import thread_api
from agent.dashboard import user_mappings as um


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str):
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value

    async def search_items(self, namespace: list[str], *, limit: int = 1000):
        ns = tuple(namespace)
        items = [{"value": v} for (n, _k), v in self.items.items() if n == ns]
        return {"items": items[:limit]}


class _FakeClient:
    def __init__(self, store: _FakeStore) -> None:
        self.store = store


@pytest.fixture()
def fake_store(monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    store = _FakeStore()
    monkeypatch.setattr(um, "_client", lambda: _FakeClient(store))
    um.clear_cache()
    return store


@pytest.mark.asyncio
async def test_run_email_prefers_github_mapping(fake_store: _FakeStore) -> None:
    await um.upsert_mapping(
        github_login="johannes117",
        work_email="johannes@langchain.dev",
        source="admin",
    )
    # OAuth profile carries a personal account that isn't an org member.
    profile = {"email": "johannesduplessis117@gmail.com"}
    assert await thread_api._resolve_run_email("johannes117", profile) == "johannes@langchain.dev"


@pytest.mark.asyncio
async def test_run_email_falls_back_to_profile_when_unmapped(fake_store: _FakeStore) -> None:
    profile = {"email": "someone@example.com"}
    assert await thread_api._resolve_run_email("nomapping", profile) == "someone@example.com"
