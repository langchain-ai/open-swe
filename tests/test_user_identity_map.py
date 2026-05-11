"""Tests for the cross-surface identity map."""

from __future__ import annotations

from typing import Any

import pytest

from agent.utils import user_identity_map


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: tuple[str, ...], key: str) -> dict[str, Any] | None:
        return self.items.get((namespace, key))

    async def put_item(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        self.items[(namespace, key)] = {"value": value}

    async def search_items(self, namespace: tuple[str, ...], limit: int = 100) -> dict[str, Any]:  # noqa: ARG002
        items = [
            {"namespace": ns, "key": key, "value": v["value"]}
            for (ns, key), v in self.items.items()
            if ns == namespace
        ]
        return {"items": items}


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(user_identity_map, "get_client", lambda url=None: client)  # noqa: ARG005
    user_identity_map._CACHE.clear()
    return client


async def test_upsert_identity_creates_new_row(fake_client: _FakeClient) -> None:
    row = await user_identity_map.upsert_identity(
        "Alice@Example.com",
        github_login="alice",
        surface="cli",
    )
    assert row is not None
    assert row["email"] == "alice@example.com"
    assert row["github_logins"] == ["alice"]
    assert "cli" in row["last_seen"]
    stored = fake_client.store.items[(("user_identity_map",), "alice@example.com")]
    assert stored["value"]["github_logins"] == ["alice"]


async def test_upsert_identity_merges_without_duplicates(fake_client: _FakeClient) -> None:
    await user_identity_map.upsert_identity(
        "alice@example.com", github_login="alice", surface="cli"
    )
    row = await user_identity_map.upsert_identity(
        "alice@example.com",
        github_login="alice",
        slack_user_id="U123",
        linear_user_id="L999",
        surface="slack",
    )
    assert row is not None
    assert row["github_logins"] == ["alice"]
    assert row["slack_user_ids"] == ["U123"]
    assert row["linear_user_ids"] == ["L999"]

    row2 = await user_identity_map.upsert_identity(
        "alice@example.com",
        github_login="alice-bot",
        slack_user_id="U123",
        surface="slack",
    )
    assert row2 is not None
    assert row2["github_logins"] == ["alice", "alice-bot"]
    assert row2["slack_user_ids"] == ["U123"]


async def test_get_identities_for_github_login_match(fake_client: _FakeClient) -> None:
    await user_identity_map.upsert_identity(
        "alice@example.com", github_login="alice", surface="cli"
    )
    row = await user_identity_map.get_identities_for_github_login("alice")
    assert row is not None
    assert row["email"] == "alice@example.com"


async def test_get_identities_for_github_login_no_match(fake_client: _FakeClient) -> None:
    await user_identity_map.upsert_identity(
        "alice@example.com", github_login="alice", surface="cli"
    )
    row = await user_identity_map.get_identities_for_github_login("nobody")
    assert row is None


async def test_round_trip_through_store(fake_client: _FakeClient) -> None:
    await user_identity_map.upsert_identity(
        "alice@example.com", github_login="alice", surface="cli"
    )
    # Drop the in-memory cache; the row should still be reachable via the store.
    user_identity_map._CACHE.clear()
    row = await user_identity_map.get_identities_for_github_login("alice")
    assert row is not None
    assert row["email"] == "alice@example.com"
    assert row["github_logins"] == ["alice"]
