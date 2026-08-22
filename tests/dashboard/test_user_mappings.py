from collections.abc import Callable

import pytest
from support.langgraph_fakes import FakeLangGraphClient, FakeStore

from agent.settings import user_mappings as um


@pytest.fixture()
def fake_store(patched_langgraph_client: Callable[..., FakeLangGraphClient]) -> FakeStore:
    um.clear_cache()
    return patched_langgraph_client().store


@pytest.mark.asyncio
async def test_upsert_and_bidirectional_lookup(fake_store: FakeStore) -> None:
    await um.upsert_mapping(
        github_login="Octocat",
        work_email="OCTO@example.com",
        slack_user_id="U123",
    )
    # Login lookups are case-insensitive; email is normalized to lowercase.
    assert await um.email_for_login("octocat") == "octo@example.com"
    assert await um.slack_id_for_login("octocat") == "U123"
    assert await um.login_for_email("octo@example.com") == "Octocat"
    assert await um.login_for_slack_id("U123") == "Octocat"


@pytest.mark.asyncio
async def test_cache_readers_after_refresh(fake_store: FakeStore) -> None:
    await um.upsert_mapping(github_login="dev", work_email="dev@x.com")
    um.clear_cache()
    await um.refresh_cache()
    assert um.cached_email_for_login("dev") == "dev@x.com"
    assert um.cached_login_for_email("dev@x.com") == "dev"
    assert um.is_login_mapped("dev") is True
    assert um.is_login_mapped("ghost") is False


@pytest.mark.asyncio
async def test_pending_status_not_trusted(fake_store: FakeStore) -> None:
    await um.upsert_mapping(github_login="newbie", work_email="n@x.com", status="pending")
    um.clear_cache()
    await um.refresh_cache()
    assert um.is_login_mapped("newbie") is False
    assert await um.slack_id_for_login("newbie") is None


@pytest.mark.asyncio
async def test_delete_removes_record_and_indexes(fake_store: FakeStore) -> None:
    await um.upsert_mapping(github_login="gone", work_email="g@x.com")
    assert await um.email_for_login("gone") == "g@x.com"
    deleted = await um.delete_mapping("gone")
    assert deleted is True
    assert um.cached_email_for_login("gone") is None
    assert await um.get_mapping("gone") is None


@pytest.mark.asyncio
async def test_resolve_login_from_email_async_cold_cache(
    fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Mapped user must resolve even on a cold worker (cache not yet primed),
    # because repo-resolution call sites run before the cache is refreshed.
    from agent.settings import agent_overrides

    monkeypatch.setattr(agent_overrides, "login_for_email", um.login_for_email)
    await um.upsert_mapping(github_login="cold", work_email="cold@x.com")
    um.clear_cache()

    assert await agent_overrides.resolve_login_from_email_async("cold@x.com") == "cold"


@pytest.mark.asyncio
async def test_update_deindexes_stale_email_and_slack_id(fake_store: FakeStore) -> None:
    # An update that changes the email/slack id must not leave the old aliases
    # resolving to this login in the in-process cache.
    await um.upsert_mapping(
        github_login="mover",
        work_email="old@x.com",
        slack_user_id="UOLD",
    )
    await um.upsert_mapping(
        github_login="mover",
        work_email="new@x.com",
        slack_user_id="UNEW",
    )

    assert um.cached_login_for_email("old@x.com") is None
    assert um.cached_login_for_slack_id("UOLD") is None
    assert um.cached_login_for_email("new@x.com") == "mover"
    assert um.cached_login_for_slack_id("UNEW") == "mover"


@pytest.mark.asyncio
async def test_upsert_requires_login_and_email(fake_store: FakeStore) -> None:
    with pytest.raises(ValueError):
        await um.upsert_mapping(github_login="", work_email="x@x.com")
    with pytest.raises(ValueError):
        await um.upsert_mapping(github_login="x", work_email="")


@pytest.mark.asyncio
async def test_list_mappings_sorted(fake_store: FakeStore) -> None:
    await um.upsert_mapping(github_login="zeta", work_email="z@x.com")
    await um.upsert_mapping(github_login="alpha", work_email="a@x.com")
    listed = await um.list_mappings()
    assert [m["github_login"] for m in listed] == ["alpha", "zeta"]
