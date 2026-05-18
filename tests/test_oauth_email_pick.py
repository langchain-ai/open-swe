"""Tests for the work-email picker used during GitHub OAuth callback."""

from __future__ import annotations

import pytest

from agent.dashboard.oauth import _pick_work_email


def test_picks_work_domain_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORK_EMAIL_DOMAIN", "langchain.dev")
    emails = [
        {"email": "personal@gmail.com", "primary": True, "verified": True},
        {"email": "work@langchain.dev", "primary": False, "verified": True},
    ]
    assert _pick_work_email(emails) == "work@langchain.dev"


def test_falls_back_to_primary_when_no_work_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORK_EMAIL_DOMAIN", "langchain.dev")
    emails = [
        {"email": "personal@gmail.com", "primary": True, "verified": True},
        {"email": "alt@example.com", "primary": False, "verified": True},
    ]
    assert _pick_work_email(emails) == "personal@gmail.com"


def test_skips_unverified_emails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORK_EMAIL_DOMAIN", "langchain.dev")
    emails = [
        {"email": "fake@langchain.dev", "primary": False, "verified": False},
        {"email": "real@gmail.com", "primary": True, "verified": True},
    ]
    assert _pick_work_email(emails) == "real@gmail.com"


def test_returns_none_for_empty_list() -> None:
    assert _pick_work_email([]) is None


def test_no_work_domain_uses_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORK_EMAIL_DOMAIN", raising=False)
    emails = [
        {"email": "a@example.com", "primary": True, "verified": True},
        {"email": "b@langchain.dev", "primary": False, "verified": True},
    ]
    assert _pick_work_email(emails) == "a@example.com"
