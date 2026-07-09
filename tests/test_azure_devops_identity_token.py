"""Tests for Entra ID token acquisition (mocked; no live Azure calls)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.utils.azure_devops import resolve_azure_devops_pat, resolve_azure_devops_pat_async
from agent.utils.azure_devops_identity_token import (
    get_azure_devops_access_token_sync,
    reset_entra_credential_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_entra_credential() -> None:
    reset_entra_credential_for_tests()
    yield
    reset_entra_credential_for_tests()


def test_get_azure_devops_access_token_sync_off_without_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_DEVOPS_USE_ENTRA_IDENTITY", raising=False)
    assert get_azure_devops_access_token_sync() is None


def test_get_azure_devops_access_token_sync_uses_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_USE_ENTRA_IDENTITY", "1")
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant")
    monkeypatch.setenv("AZURE_CLIENT_ID", "client")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret")

    mock_cred = MagicMock()
    mock_cred.get_token.return_value = SimpleNamespace(token="jwt-from-mock", expires_on=9999999999)

    monkeypatch.setattr(
        "agent.utils.azure_devops_identity_token._build_credential",
        lambda: mock_cred,
    )

    assert get_azure_devops_access_token_sync() == "jwt-from-mock"
    mock_cred.get_token.assert_called_once()


def test_resolve_azure_devops_pat_prefers_env_over_entra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_PAT", "static-pat")
    monkeypatch.setenv("AZURE_DEVOPS_USE_ENTRA_IDENTITY", "1")
    monkeypatch.setenv("AZURE_TENANT_ID", "t")
    monkeypatch.setenv("AZURE_CLIENT_ID", "c")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "s")

    assert resolve_azure_devops_pat(None) == "static-pat"


def test_resolve_azure_devops_pat_entra_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_DEVOPS_PAT", raising=False)
    monkeypatch.setattr(
        "agent.utils.azure_devops_identity_token.get_azure_devops_access_token_sync",
        lambda: "entra-token",
    )

    assert resolve_azure_devops_pat(None) == "entra-token"


def test_resolve_azure_devops_pat_async_entra_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_DEVOPS_PAT", raising=False)

    async def fake_entra() -> str:
        return "entra-async"

    monkeypatch.setattr(
        "agent.utils.azure_devops_identity_token.get_azure_devops_access_token_async",
        fake_entra,
    )

    assert asyncio.run(resolve_azure_devops_pat_async(None)) == "entra-async"


def test_resolve_azure_devops_pat_async_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_PAT", "env-pat")
    assert asyncio.run(resolve_azure_devops_pat_async(None)) == "env-pat"
