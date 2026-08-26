"""Shared pytest fixtures."""

from collections.abc import Iterator

import pytest

from agent.utils import ttl_cache
from agent.webhooks import common as webhook_common


@pytest.fixture(autouse=True)
async def _isolated_thread_registry(monkeypatch: pytest.MonkeyPatch):
    from agent.dashboard.thread_registry import close_thread_registry

    monkeypatch.setenv("OPEN_SWE_REGISTRY_SQLITE_PATH", ":memory:")
    await close_thread_registry()
    try:
        yield
    finally:
        await close_thread_registry()


@pytest.fixture(autouse=True)
def _reset_ttl_cache() -> Iterator[None]:
    """Keep the process-global TTL cache from leaking team settings between tests."""
    ttl_cache.clear()
    yield
    ttl_cache.clear()


@pytest.fixture(autouse=True)
def _default_enable_auto_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat automatic reviews as enabled for every repo by default.

    The dashboard's opt-in list (loaded by :func:`agent.dashboard.enabled_repos.is_review_repo_enabled`)
    is empty in the test environment because there is no live LangGraph Store.

    Tests targeting the automatic-review gate should override this fixture or set
    ``monkeypatch.setattr(webhook_common, "is_review_repo_enabled", ...)`` to a stricter stub.
    """

    async def _enabled(_owner: str, _name: str) -> bool:
        return True

    monkeypatch.setattr(webhook_common, "is_review_repo_enabled", _enabled)
