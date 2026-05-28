"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from agent import webapp


@pytest.fixture(autouse=True)
def _default_enable_review_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat every repo as enabled for review by default.

    The dashboard's opt-in list (loaded by :func:`agent.dashboard.enabled_repos.is_review_repo_enabled`)
    is empty in the test environment because there is no live LangGraph Store.

    Tests targeting the opt-in gate itself should override this fixture or set
    ``monkeypatch.setattr(webapp, "is_review_repo_enabled", ...)`` to a stricter stub.
    """

    async def _enabled(_owner: str, _name: str) -> bool:
        return True

    monkeypatch.setattr(webapp, "is_review_repo_enabled", _enabled)
