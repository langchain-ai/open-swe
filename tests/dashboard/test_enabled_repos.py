"""Tests for the enabled-review-repos store lookup used by the auto-review gate."""

from __future__ import annotations

import logging

import pytest

from agent.dashboard import enabled_repos


class _FakeStore:
    def __init__(self, *, item=None, error: Exception | None = None) -> None:
        self._item = item
        self._error = error

    async def get_item(self, _namespace: list[str], _key: str):
        if self._error is not None:
            raise self._error
        return self._item


class _FakeClient:
    def __init__(self, store: _FakeStore) -> None:
        self.store = store


@pytest.mark.asyncio
async def test_list_enabled_review_repos_logs_exception_on_store_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(
        enabled_repos, "_client", lambda: _FakeClient(_FakeStore(error=RuntimeError("boom")))
    )

    with caplog.at_level(logging.ERROR, logger="agent.dashboard.enabled_repos"):
        repos = await enabled_repos.list_enabled_review_repos()

    assert repos == []
    assert any("enabled review repos lookup failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_is_review_repo_enabled_logs_result(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake_item = {"value": {"repos": ["aeteq/open-swe"]}}
    monkeypatch.setattr(enabled_repos, "_client", lambda: _FakeClient(_FakeStore(item=fake_item)))

    with caplog.at_level(logging.INFO, logger="agent.dashboard.enabled_repos"):
        enabled = await enabled_repos.is_review_repo_enabled("Aeteq", "Open-SWE")
        disabled = await enabled_repos.is_review_repo_enabled("aeteq", "other-repo")

    assert enabled is True
    assert disabled is False
    assert any(
        "Auto-review enabled check for aeteq/open-swe: True" in r.message for r in caplog.records
    )
    assert any(
        "Auto-review enabled check for aeteq/other-repo: False" in r.message for r in caplog.records
    )
