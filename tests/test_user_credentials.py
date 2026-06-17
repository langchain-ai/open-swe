from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from agent.dashboard import user_credentials as uc
from agent.dashboard.user_credentials import CurrentsCredentialsUpdate


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str):
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value

    async def delete_item(self, namespace: list[str], key: str) -> None:
        self.items.pop((tuple(namespace), key), None)


class _FakeClient:
    def __init__(self, store: _FakeStore) -> None:
        self.store = store


@pytest.fixture()
def fake_store(monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    store = _FakeStore()
    monkeypatch.setattr(uc, "_client", lambda: _FakeClient(store))
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return store


class TestValidators:
    def test_empty_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CurrentsCredentialsUpdate(api_key="")

    def test_whitespace_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CurrentsCredentialsUpdate(api_key="  ")

    def test_key_trimmed(self) -> None:
        u = CurrentsCredentialsUpdate(api_key="  secret  ")
        assert u.api_key == "secret"


@pytest.mark.asyncio
async def test_currents_roundtrip_and_redaction(fake_store: _FakeStore) -> None:
    status = await uc.connect_currents(
        "alice", CurrentsCredentialsUpdate(api_key="secret-currents-key-1234")
    )
    assert status["currents"]["connected"] is True
    assert status["currents"]["api_key_last4"] == "1234"

    record = fake_store.items[(("user_credentials", "alice"), "currents")]
    assert record["encrypted_api_key"] != "secret-currents-key-1234"

    api_key = await uc.get_currents_api_key("alice")
    assert api_key == "secret-currents-key-1234"

    after = await uc.disconnect_currents("alice")
    assert after["currents"]["connected"] is False
    assert await uc.get_currents_api_key("alice") is None


@pytest.mark.asyncio
async def test_currents_isolation_between_users(fake_store: _FakeStore) -> None:
    await uc.connect_currents("alice", CurrentsCredentialsUpdate(api_key="alice-key-abcd"))
    await uc.connect_currents("bob", CurrentsCredentialsUpdate(api_key="bob-key-wxyz"))

    assert await uc.get_currents_api_key("alice") == "alice-key-abcd"
    assert await uc.get_currents_api_key("bob") == "bob-key-wxyz"

    await uc.disconnect_currents("alice")
    assert await uc.get_currents_api_key("alice") is None
    assert await uc.get_currents_api_key("bob") == "bob-key-wxyz"


@pytest.mark.asyncio
async def test_currents_status_when_not_connected(fake_store: _FakeStore) -> None:
    status = await uc.get_currents_status("nobody")
    assert status["currents"]["connected"] is False


@pytest.mark.asyncio
async def test_get_currents_api_key_none_when_not_connected(fake_store: _FakeStore) -> None:
    assert await uc.get_currents_api_key("nobody") is None
