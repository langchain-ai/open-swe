from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from openswe.database import store_imports
from openswe.database.store_imports import StoreImport
from openswe.slack import import_allowed_bots
from openswe.slack.allowed_bots import AllowedSlackBot, resolve_allowed_slack_bot
from openswe.slack.import_allowed_bots import import_store_allowed_slack_bots
from openswe.store import StoreEntry
from tests.conftest import FakeStore

NAMESPACE = ("allowed_slack_bots",)


@pytest.fixture
def store(registry_db: None, fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    async def search(namespace: Sequence[str]) -> list[StoreEntry]:
        return [
            StoreEntry(list(NAMESPACE), dict(value), key)
            for key, value in fake_store.items.get(tuple(namespace), {}).items()
        ]

    monkeypatch.setattr(import_allowed_bots, "search_all_entries", search)
    return fake_store


def _record(bot_id: str, created_at: str = "2026-09-09T00:00:00+00:00") -> dict[str, str]:
    return {
        "team_id": "T1",
        "bot_id": bot_id,
        "user_id": f"U{bot_id[1:]}",
        "name": bot_id,
        "created_at": created_at,
    }


async def test_store_import_copies_bots_then_retires_the_store_records(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.seed(NAMESPACE, "T1:B1", _record("B1"))
    store.seed(NAMESPACE, "T1:B2", _record("B2"))
    store.seed(NAMESPACE, "T2:B3", _record("B3"))

    assert await import_store_allowed_slack_bots() == StoreImport(moved=2)
    assert await resolve_allowed_slack_bot("T1", "B1", user_id="U1") is not None
    assert await resolve_allowed_slack_bot("T2", "B3") is None
    assert await import_store_allowed_slack_bots() == StoreImport(waiting=2)

    # An old replica allows a revoked bot again under a new identity.
    store.seed(
        NAMESPACE, "T1:B2", {**_record("B2", datetime.now(UTC).isoformat()), "user_id": "U9"}
    )
    assert await import_store_allowed_slack_bots() == StoreImport(moved=1, waiting=1)
    assert await resolve_allowed_slack_bot("T1", "B2", user_id="U9") is not None

    monkeypatch.setattr(store_imports, "RETIRE_STORE_COPY_AFTER", timedelta())
    assert await import_store_allowed_slack_bots() == StoreImport(moved=2)
    assert set(store.items[NAMESPACE]) == {"T2:B3"}
    assert [bot.bot_id for bot in await AllowedSlackBot.all()] == ["B1", "B2"]


async def test_store_import_revokes_copies_an_old_replica_revoked_in_the_store(
    store: FakeStore,
) -> None:
    store.seed(NAMESPACE, "T1:B1", _record("B1"))
    store.seed(NAMESPACE, "T1:B2", _record("B2"))
    assert await import_store_allowed_slack_bots() == StoreImport(moved=2)
    added = AllowedSlackBot(team_id="T1", bot_id="B3", name="B3", created_at=datetime.now(UTC))
    assert await added.add()

    store.items[NAMESPACE].pop("T1:B1")

    assert await import_store_allowed_slack_bots() == StoreImport(moved=1, waiting=1)
    assert await resolve_allowed_slack_bot("T1", "B1") is None
    assert [bot.bot_id for bot in await AllowedSlackBot.all()] == ["B2", "B3"]

    # A revocation in this release also removes the Store record the import copies from.
    await AllowedSlackBot.remove("T1", "B2")
    assert await import_store_allowed_slack_bots() == StoreImport()
    assert [bot.bot_id for bot in await AllowedSlackBot.all()] == ["B3"]
