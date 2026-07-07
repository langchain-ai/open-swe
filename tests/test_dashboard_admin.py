from __future__ import annotations

import pytest

from agent.dashboard.admin import is_admin, is_observability_authorized


def test_is_admin_accepts_email_or_github_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "Alice, bob@langchain.dev")

    assert is_admin("bob@langchain.dev", login="not-bob") is True
    assert is_admin("other@langchain.dev", login="alice") is True
    assert is_admin("other@langchain.dev", login="ALICE") is True
    assert is_admin("other@langchain.dev", login="mallory") is False


def test_is_admin_rejects_blank_identities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "alice")

    assert is_admin(None, login=None) is False
    assert is_admin("", login=" ") is False


def test_observability_authorized_treats_admin_login_as_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "alice")
    monkeypatch.delenv("OBSERVABILITY_AUTHORIZED_EMAILS", raising=False)

    assert is_observability_authorized(None, login="alice") is True
    assert is_observability_authorized("other@langchain.dev", login="mallory") is False


def test_observability_allowlist_still_uses_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "")
    monkeypatch.setenv("OBSERVABILITY_AUTHORIZED_EMAILS", "trusted@langchain.dev")

    assert is_observability_authorized("trusted@langchain.dev", login="mallory") is True
    assert is_observability_authorized("other@langchain.dev", login="trusted") is False
