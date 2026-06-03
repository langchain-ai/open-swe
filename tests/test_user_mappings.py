from __future__ import annotations

from typing import Any

import pytest

from agent.dashboard import user_mappings as um


class _FakeStore:
    """Minimal in-memory stand-in for the LangGraph Store."""

    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str):
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value

    async def delete_item(self, namespace: list[str], key: str) -> None:
        self.items.pop((tuple(namespace), key), None)

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
async def test_upsert_and_bidirectional_lookup(fake_store: _FakeStore) -> None:
    await um.upsert_mapping(
        github_login="Octocat",
        work_email="OCTO@example.com",
        slack_user_id="U123",
        source="admin",
    )
    # Login lookups are case-insensitive; email is normalized to lowercase.
    assert await um.email_for_login("octocat") == "octo@example.com"
    assert await um.login_for_email("octo@example.com") == "Octocat"
    assert await um.login_for_slack_id("U123") == "Octocat"


@pytest.mark.asyncio
async def test_cache_readers_after_refresh(fake_store: _FakeStore) -> None:
    await um.upsert_mapping(github_login="dev", work_email="dev@x.com", source="admin")
    um.clear_cache()
    await um.refresh_cache()
    assert um.cached_email_for_login("dev") == "dev@x.com"
    assert um.cached_login_for_email("dev@x.com") == "dev"
    assert um.is_login_mapped("dev") is True
    assert um.is_login_mapped("ghost") is False


@pytest.mark.asyncio
async def test_pending_status_not_trusted(fake_store: _FakeStore) -> None:
    await um.upsert_mapping(
        github_login="newbie", work_email="n@x.com", source="self", status="pending"
    )
    um.clear_cache()
    await um.refresh_cache()
    assert um.is_login_mapped("newbie") is False


@pytest.mark.asyncio
async def test_delete_removes_record_and_indexes(fake_store: _FakeStore) -> None:
    await um.upsert_mapping(github_login="gone", work_email="g@x.com", source="admin")
    assert await um.email_for_login("gone") == "g@x.com"
    deleted = await um.delete_mapping("gone")
    assert deleted is True
    assert um.cached_email_for_login("gone") is None
    assert await um.get_mapping("gone") is None


@pytest.mark.asyncio
async def test_resolve_login_from_email_async_cold_cache(
    fake_store: _FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Mapped user must resolve even on a cold worker (cache not yet primed),
    # because repo-resolution call sites run before the cache is refreshed.
    from agent.dashboard import agent_overrides

    monkeypatch.setattr(agent_overrides, "login_for_email", um.login_for_email)
    await um.upsert_mapping(github_login="cold", work_email="cold@x.com", source="admin")
    um.clear_cache()

    assert await agent_overrides.resolve_login_from_email_async("cold@x.com") == "cold"


@pytest.mark.asyncio
async def test_update_deindexes_stale_email_and_slack_id(fake_store: _FakeStore) -> None:
    # An update that changes the email/slack id must not leave the old aliases
    # resolving to this login in the in-process cache.
    await um.upsert_mapping(
        github_login="mover",
        work_email="old@x.com",
        slack_user_id="UOLD",
        source="admin",
    )
    await um.upsert_mapping(
        github_login="mover",
        work_email="new@x.com",
        slack_user_id="UNEW",
        source="admin",
    )

    assert um.cached_login_for_email("old@x.com") is None
    assert um.cached_login_for_slack_id("UOLD") is None
    assert um.cached_login_for_email("new@x.com") == "mover"
    assert um.cached_login_for_slack_id("UNEW") == "mover"


@pytest.mark.asyncio
async def test_bulk_import_skips_existing(fake_store: _FakeStore) -> None:
    # Pre-existing richer record must survive a legacy re-import.
    await um.upsert_mapping(github_login="keep", work_email="keep@x.com", source="self")
    created = await um.bulk_import(
        {"keep": "legacy@x.com", "fresh": "fresh@x.com", "bad": ""},
        source="hardcoded",
    )
    assert created == 1
    keep = await um.get_mapping("keep")
    assert keep["work_email"] == "keep@x.com"
    assert keep["source"] == "self"
    fresh = await um.get_mapping("fresh")
    assert fresh["source"] == "hardcoded"


@pytest.mark.asyncio
async def test_upsert_requires_login_and_email(fake_store: _FakeStore) -> None:
    with pytest.raises(ValueError):
        await um.upsert_mapping(github_login="", work_email="x@x.com")
    with pytest.raises(ValueError):
        await um.upsert_mapping(github_login="x", work_email="")


@pytest.mark.asyncio
async def test_list_mappings_sorted(fake_store: _FakeStore) -> None:
    await um.upsert_mapping(github_login="zeta", work_email="z@x.com", source="admin")
    await um.upsert_mapping(github_login="alpha", work_email="a@x.com", source="admin")
    listed = await um.list_mappings()
    assert [m["github_login"] for m in listed] == ["alpha", "zeta"]
