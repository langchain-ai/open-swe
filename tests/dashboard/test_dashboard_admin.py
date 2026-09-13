import pytest

from agent.dashboard.admin import is_admin


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
