"""Unit tests for Slack ↔ actor identity link Store."""

from __future__ import annotations

import asyncio

import pytest

from managed_deepagents import _identity_links
from managed_deepagents._identity_links import (
    InMemoryIdentityLinkStore,
    build_identity_link_record,
    set_identity_link_store,
    slack_key_from_parts,
)


def setup_function() -> None:
    set_identity_link_store(None)


def teardown_function() -> None:
    set_identity_link_store(None)


def test_upsert_and_lookup() -> None:
    async def _run() -> None:
        store = InMemoryIdentityLinkStore()
        record = build_identity_link_record(
            user_id="alice@example.com",
            slack_user_id="U1",
            slack_team_id="T1",
            email="alice@example.com",
        )
        assert record.slack_key == slack_key_from_parts("T1", "U1")
        await store.upsert(record)
        assert await store.get_by_slack_key(record.slack_key) == record
        assert await store.get_by_user_id("alice@example.com") == record

    asyncio.run(_run())


def test_reconnect_replaces_prior_slack_id() -> None:
    async def _run() -> None:
        store = InMemoryIdentityLinkStore()
        await store.upsert(
            build_identity_link_record(
                user_id="alice@example.com", slack_user_id="U1", slack_team_id="T1"
            )
        )
        await store.upsert(
            build_identity_link_record(
                user_id="alice@example.com", slack_user_id="U2", slack_team_id="T1"
            )
        )
        assert await store.get_by_slack_key("slack:T1:U1") is None
        linked = await store.get_by_slack_key("slack:T1:U2")
        assert linked is not None
        assert linked.user_id == "alice@example.com"

    asyncio.run(_run())


def test_remove() -> None:
    async def _run() -> None:
        store = InMemoryIdentityLinkStore()
        record = build_identity_link_record(
            user_id="alice@example.com", slack_user_id="U1", slack_team_id="T1"
        )
        await store.upsert(record)
        await store.remove_by_user_id("alice@example.com")
        assert await store.get_by_user_id("alice@example.com") is None
        assert await store.get_by_slack_key(record.slack_key) is None

    asyncio.run(_run())


def test_handoff_secret_is_not_used_for_remote_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("MDA_INGRESS_SECRET", raising=False)
    monkeypatch.setenv("MDA_HANDOFF_SECRET", "generated-handoff-secret")
    monkeypatch.setenv("MDA_CHANNEL_API_URL", "https://proxy.example")

    assert _identity_links.create_langgraph_identity_link_store() is None
