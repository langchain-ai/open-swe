from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from agent import store as agent_store
from agent.dashboard import team_credentials as tc
from agent.dashboard.team_credentials import (
    LangSmithCredentialsUpdate,
)


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
    monkeypatch.setattr(agent_store, "store_client", lambda: _FakeClient(store))
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return store


class TestValidators:
    def test_empty_keys_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LangSmithCredentialsUpdate(api_key="")

    def test_langsmith_endpoint_normalized(self) -> None:
        u = LangSmithCredentialsUpdate(api_key="k", endpoint="https://x/")
        assert u.endpoint == "https://x"


@pytest.mark.asyncio
async def test_langsmith_roundtrip(fake_store: _FakeStore) -> None:
    status = await tc.connect_langsmith(LangSmithCredentialsUpdate(api_key="ls-key-9999"))
    assert status["langsmith"]["connected"] is True
    assert status["langsmith"]["api_key_last4"] == "9999"

    creds = await tc.get_langsmith_credentials()
    assert creds is not None
    assert creds.api_key == "ls-key-9999"
    assert creds.endpoint == tc.DEFAULT_LANGSMITH_ENDPOINT

    await tc.disconnect_langsmith()
    assert await tc.get_langsmith_credentials() is None


@pytest.mark.asyncio
async def test_status_empty_when_unset(fake_store: _FakeStore) -> None:
    status = await tc.get_team_credentials_status()
    assert status == {"langsmith": {"connected": False}}
