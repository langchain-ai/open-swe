from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from agent.dashboard import team_credentials as tc
from agent.dashboard.team_credentials import (
    DatadogCredentialsUpdate,
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


class _FakeClient:
    def __init__(self, store: _FakeStore) -> None:
        self.store = store


@pytest.fixture()
def fake_store(monkeypatch: pytest.MonkeyPatch) -> _FakeStore:
    store = _FakeStore()
    monkeypatch.setattr(tc, "_client", lambda: _FakeClient(store))
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    return store


class TestValidators:
    def test_site_normalized(self) -> None:
        u = DatadogCredentialsUpdate(site="https://app.datadoghq.com/", api_key="k", app_key="a")
        assert u.site == "datadoghq.com"

    def test_site_default(self) -> None:
        u = DatadogCredentialsUpdate(api_key="k", app_key="a")
        assert u.site == "datadoghq.com"

    def test_unsupported_site_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DatadogCredentialsUpdate(site="evil.example.com", api_key="k", app_key="a")

    def test_empty_keys_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DatadogCredentialsUpdate(api_key="  ", app_key="a")
        with pytest.raises(ValidationError):
            LangSmithCredentialsUpdate(api_key="")

    def test_langsmith_endpoint_normalized(self) -> None:
        u = LangSmithCredentialsUpdate(api_key="k", endpoint="https://x/")
        assert u.endpoint == "https://x"


class TestMcpUrl:
    def test_url_includes_site_and_toolsets(self) -> None:
        creds = tc.DatadogCredentials(site="datadoghq.eu", api_key="a", app_key="b")
        assert creds.mcp_url("core") == (
            "https://mcp.datadoghq.eu/api/unstable/mcp-server/mcp?toolsets=core"
        )


@pytest.mark.asyncio
async def test_datadog_roundtrip_and_redaction(fake_store: _FakeStore) -> None:
    status = await tc.connect_datadog(
        DatadogCredentialsUpdate(site="datadoghq.com", api_key="secret-api-1234", app_key="appk")
    )
    assert status["datadog"]["connected"] is True
    assert status["datadog"]["api_key_last4"] == "1234"
    # Stored record holds ciphertext, not the plaintext key.
    record = fake_store.items[(("team_credentials",), "default")]
    assert record["datadog"]["encrypted_api_key"] != "secret-api-1234"

    creds = await tc.get_datadog_credentials()
    assert creds is not None
    assert creds.api_key == "secret-api-1234"
    assert creds.app_key == "appk"

    after = await tc.disconnect_datadog()
    assert after["datadog"]["connected"] is False
    assert await tc.get_datadog_credentials() is None


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
    assert status["datadog"]["connected"] is False
    assert status["langsmith"]["connected"] is False


@pytest.mark.asyncio
async def test_connecting_one_keeps_other(fake_store: _FakeStore) -> None:
    await tc.connect_datadog(DatadogCredentialsUpdate(api_key="dd", app_key="app"))
    await tc.connect_langsmith(LangSmithCredentialsUpdate(api_key="ls"))
    status = await tc.get_team_credentials_status()
    assert status["datadog"]["connected"] is True
    assert status["langsmith"]["connected"] is True
