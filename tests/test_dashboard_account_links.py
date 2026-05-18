"""Tests for the dashboard account-link store helpers and email index."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from agent.dashboard import account_links, profiles


class _NotFound(httpx.HTTPStatusError):
    def __init__(self) -> None:
        request = httpx.Request("GET", "http://test")
        response = httpx.Response(404, request=request)
        super().__init__("not found", request=request, response=response)


class _InMemoryStore:
    def __init__(self) -> None:
        self._data: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str) -> dict[str, Any]:
        ns = tuple(namespace)
        if (ns, key) not in self._data:
            raise _NotFound()
        return {"value": self._data[(ns, key)]}

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        ns = tuple(namespace)
        self._data[(ns, key)] = value

    async def delete_item(self, namespace: list[str], key: str) -> None:
        ns = tuple(namespace)
        if (ns, key) not in self._data:
            raise _NotFound()
        del self._data[(ns, key)]

    async def search_items(self, namespace: list[str], *, limit: int = 1000) -> dict[str, Any]:
        ns = tuple(namespace)
        items = [{"value": v} for (n, _), v in self._data.items() if n == ns][:limit]
        return {"items": items}


class _FakeClient:
    def __init__(self, store: _InMemoryStore) -> None:
        self.store = store


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> _InMemoryStore:
    store = _InMemoryStore()
    client = _FakeClient(store)
    monkeypatch.setattr(account_links, "_client", lambda: client)
    monkeypatch.setattr(profiles, "_client", lambda: client)
    return store


async def test_slack_link_roundtrip(fake_store: _InMemoryStore) -> None:
    await account_links.upsert_slack_link(
        github_login="johannes117",
        slack_user_id="U01ABC",
        slack_team_id="T01XYZ",
        slack_email="johannes@langchain.dev",
    )

    found = await account_links.get_slack_link_by_user("U01ABC")
    assert found is not None
    assert found["github_login"] == "johannes117"
    assert found["slack_email"] == "johannes@langchain.dev"

    links = await account_links.get_links_for_login("johannes117")
    assert links["slack"] is not None
    assert links["slack"]["slack_user_id"] == "U01ABC"
    assert links["linear"] is None


async def test_linear_link_delete(fake_store: _InMemoryStore) -> None:
    await account_links.upsert_linear_link(
        github_login="johannes117",
        linear_user_id="lin-user-123",
        linear_workspace_id="lin-ws-1",
        linear_email="johannes@langchain.dev",
    )
    removed = await account_links.delete_link_for_login("linear", "johannes117")
    assert removed is True

    again = await account_links.delete_link_for_login("linear", "johannes117")
    assert again is False

    assert await account_links.get_linear_link_by_user("lin-user-123") is None


async def test_email_index_tracks_oauth_token_writes(fake_store: _InMemoryStore) -> None:
    await profiles.upsert_email_record("johannes117", "Johannes@LangChain.dev")
    assert await profiles.get_email_for_github_login("johannes117") == "johannes@langchain.dev"
    assert await profiles.get_login_for_email("johannes@langchain.dev") == "johannes117"
    assert await profiles.get_login_for_email("JOHANNES@LANGCHAIN.DEV") == "johannes117"


async def test_email_index_updates_when_email_changes(fake_store: _InMemoryStore) -> None:
    await profiles.upsert_email_record("octocat", "old@example.com")
    assert await profiles.get_login_for_email("old@example.com") == "octocat"

    await profiles.upsert_email_record("octocat", "new@example.com")
    assert await profiles.get_email_for_github_login("octocat") == "new@example.com"
    assert await profiles.get_login_for_email("new@example.com") == "octocat"
    assert await profiles.get_login_for_email("old@example.com") is None


async def test_get_email_for_missing_login_returns_none(fake_store: _InMemoryStore) -> None:
    assert await profiles.get_email_for_github_login("does-not-exist") is None
    assert await profiles.get_login_for_email("nobody@example.com") is None
