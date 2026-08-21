"""Direct tests for the shared OAuth token vault.

The concurrency behaviour (per-login lock, double-checked refresh, dropping a
dead authorization without clobbering a concurrent re-authorization) is subtle
and identical for every provider, so it is exercised here once against a
synthetic provider rather than once per provider.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.fernet import Fernet
from support.langgraph_fakes import FakeLangGraphClient, FakeStore

from agent.dashboard.token_vault import TokenFields, TokenVault, expires_at_from_response
from agent.encryption import encrypt_token

NAMESPACE = ("demo_tokens",)
FIELDS = TokenFields(access="enc_access", refresh="enc_refresh", expires_at="expires")


class DeadRefreshToken(Exception):
    """The synthetic provider's "re-authorization required" error."""


class TransientRefreshFailure(Exception):
    """The synthetic provider's retryable error."""


@pytest.fixture()
def fake_store(
    patched_langgraph_client: Callable[..., FakeLangGraphClient],
    monkeypatch: pytest.MonkeyPatch,
) -> FakeStore:
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return patched_langgraph_client().store


def _in(**delta: float) -> str:
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()


def _record(access: str, refresh: str, expires_at: str | None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "enc_access": encrypt_token(access),
        "enc_refresh": encrypt_token(refresh),
    }
    if expires_at is not None:
        record["expires"] = expires_at
    return record


def _vault(refresh: Any) -> TokenVault:
    return TokenVault(
        "Demo",
        locate=lambda login: (NAMESPACE, login),
        fields=FIELDS,
        refresh=refresh,
        is_permanently_dead=lambda exc: isinstance(exc, DeadRefreshToken),
    )


async def _never_refresh(*, login: str, refresh_token: str, record: dict[str, Any]) -> Any:
    raise AssertionError(f"refresh should not run for {login}")


def test_expires_at_from_response() -> None:
    expires = expires_at_from_response({"expires_in": 3600}, field="expires_in")
    assert expires is not None
    assert datetime.fromisoformat(expires) > datetime.now(UTC)
    assert expires_at_from_response({"refresh_token_expires_in": 0}) is None
    assert expires_at_from_response({"expires_in": "3600"}) is None
    assert expires_at_from_response({}) is None


def test_needs_refresh_applies_skew() -> None:
    vault = _vault(_never_refresh)
    assert vault.needs_refresh({"expires": _in(minutes=-1)}) is True
    assert vault.needs_refresh({"expires": _in(minutes=4)}) is True
    assert vault.needs_refresh({"expires": _in(hours=2)}) is False
    assert vault.needs_refresh({}) is False
    assert vault.needs_refresh({"expires": "not-a-timestamp"}) is False


@pytest.mark.asyncio
async def test_record_crud_round_trip(fake_store: FakeStore) -> None:
    vault = _vault(_never_refresh)
    assert await vault.get_record("octo") is None

    record = _record("access", "refresh", _in(hours=5))
    await vault.store_record("octo", record)
    assert await vault.get_record("octo") == record
    assert fake_store.items[(NAMESPACE, "octo")] == record

    await vault.delete_record("octo")
    assert await vault.get_record("octo") is None


@pytest.mark.asyncio
async def test_get_valid_returns_stored_record_when_not_expiring(fake_store: FakeStore) -> None:
    fake_store.items[(NAMESPACE, "octo")] = _record("still-good", "refresh", _in(hours=5))
    vault = _vault(_never_refresh)

    record = await vault.get_valid("octo")

    assert record is not None
    assert vault.access_token(record) == "still-good"


@pytest.mark.asyncio
async def test_get_valid_is_none_without_a_record(fake_store: FakeStore) -> None:
    assert await _vault(_never_refresh).get_valid("nobody") is None


@pytest.mark.asyncio
async def test_get_valid_is_none_when_the_access_token_will_not_decrypt(
    fake_store: FakeStore,
) -> None:
    fake_store.items[(NAMESPACE, "octo")] = {
        "enc_access": "encrypted-under-a-key-we-no-longer-have",
        "enc_refresh": encrypt_token("refresh"),
        "expires": _in(hours=5),
    }

    assert await _vault(_never_refresh).get_valid("octo") is None


@pytest.mark.asyncio
async def test_get_valid_refreshes_when_near_expiry(fake_store: FakeStore) -> None:
    fake_store.items[(NAMESPACE, "octo")] = _record("old-access", "old-refresh", _in(minutes=1))
    seen: list[str] = []

    async def refresh(*, login: str, refresh_token: str, record: dict[str, Any]) -> dict[str, Any]:
        seen.append(refresh_token)
        return _record("new-access", "new-refresh", _in(hours=8))

    vault = _vault(refresh)
    refreshed = await vault.get_valid("octo")

    assert refreshed is not None
    assert vault.access_token(refreshed) == "new-access"
    assert seen == ["old-refresh"]
    stored = fake_store.items[(NAMESPACE, "octo")]
    assert vault.access_token(stored) == "new-access"
    assert vault.refresh_token(stored) == "new-refresh"


@pytest.mark.asyncio
async def test_force_refresh_rotates_a_token_that_is_nowhere_near_expiry(
    fake_store: FakeStore,
) -> None:
    fake_store.items[(NAMESPACE, "octo")] = _record("old-access", "old-refresh", _in(hours=8))

    async def refresh(*, login: str, refresh_token: str, record: dict[str, Any]) -> dict[str, Any]:
        return _record("new-access", "new-refresh", _in(hours=8))

    vault = _vault(refresh)
    refreshed = await vault.get_valid("octo", force_refresh=True)

    assert refreshed is not None
    assert vault.access_token(refreshed) == "new-access"


@pytest.mark.asyncio
async def test_get_valid_keeps_an_expired_record_that_has_no_refresh_token(
    fake_store: FakeStore,
) -> None:
    fake_store.items[(NAMESPACE, "octo")] = {
        "enc_access": encrypt_token("expiring-access"),
        "expires": _in(minutes=-1),
    }
    vault = _vault(_never_refresh)

    record = await vault.get_valid("octo")

    assert record is not None
    assert vault.access_token(record) == "expiring-access"


@pytest.mark.asyncio
async def test_get_valid_keeps_the_record_on_a_transient_refresh_failure(
    fake_store: FakeStore,
) -> None:
    fake_store.items[(NAMESPACE, "octo")] = _record("still-usable", "refresh", _in(minutes=1))

    async def refresh(*, login: str, refresh_token: str, record: dict[str, Any]) -> dict[str, Any]:
        raise TransientRefreshFailure("upstream is down")

    vault = _vault(refresh)
    kept = await vault.get_valid("octo")

    assert kept is not None
    assert vault.access_token(kept) == "still-usable"
    assert (NAMESPACE, "octo") in fake_store.items


@pytest.mark.asyncio
async def test_get_valid_drops_the_record_when_the_refresh_token_is_dead(
    fake_store: FakeStore,
) -> None:
    fake_store.items[(NAMESPACE, "octo")] = _record("stale-access", "dead-refresh", _in(minutes=1))

    async def refresh(*, login: str, refresh_token: str, record: dict[str, Any]) -> dict[str, Any]:
        raise DeadRefreshToken("revoked")

    assert await _vault(refresh).get_valid("octo") is None
    assert (NAMESPACE, "octo") not in fake_store.items
    assert fake_store.deleted == [(NAMESPACE, "octo")]


@pytest.mark.asyncio
async def test_a_dead_refresh_token_does_not_drop_a_concurrent_reauthorization(
    fake_store: FakeStore,
) -> None:
    fake_store.items[(NAMESPACE, "octo")] = _record("stale-access", "dead-refresh", _in(minutes=1))

    async def refresh(*, login: str, refresh_token: str, record: dict[str, Any]) -> dict[str, Any]:
        # The OAuth callback lands while the refresh request is in flight.
        fake_store.items[(NAMESPACE, "octo")] = _record(
            "reauthed-access", "fresh-refresh", _in(hours=8)
        )
        raise DeadRefreshToken("revoked")

    vault = _vault(refresh)
    record = await vault.get_valid("octo")

    assert record is not None
    assert vault.access_token(record) == "reauthed-access"
    assert fake_store.deleted == []


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_refresh(fake_store: FakeStore) -> None:
    fake_store.items[(NAMESPACE, "octo")] = _record("old-access", "old-refresh", _in(minutes=1))
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[str] = []

    async def refresh(*, login: str, refresh_token: str, record: dict[str, Any]) -> dict[str, Any]:
        calls.append(refresh_token)
        started.set()
        await release.wait()
        return _record("new-access", "new-refresh", _in(hours=8))

    vault = _vault(refresh)
    first = asyncio.create_task(vault.get_valid("octo"))
    await started.wait()
    second = asyncio.create_task(vault.get_valid("octo"))
    release.set()

    first_record, second_record = await asyncio.gather(first, second)

    assert calls == ["old-refresh"]
    assert first_record is not None
    assert second_record is not None
    assert vault.access_token(first_record) == "new-access"
    assert vault.access_token(second_record) == "new-access"


@pytest.mark.asyncio
async def test_refreshes_are_keyed_by_login(fake_store: FakeStore) -> None:
    for login in ("octo", "hubot"):
        fake_store.items[(NAMESPACE, login)] = _record(
            f"{login}-old", f"{login}-refresh", _in(minutes=1)
        )

    async def refresh(*, login: str, refresh_token: str, record: dict[str, Any]) -> dict[str, Any]:
        return _record(f"{login}-new", refresh_token, _in(hours=8))

    vault = _vault(refresh)
    octo, hubot = await asyncio.gather(vault.get_valid("octo"), vault.get_valid("hubot"))

    assert octo is not None
    assert hubot is not None
    assert vault.access_token(octo) == "octo-new"
    assert vault.access_token(hubot) == "hubot-new"
