"""Shared fixtures for the Slack tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.slack import mda_forwarding


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "mda_lookups: use MDA forwarding's real thread lookups")


@pytest.fixture(autouse=True)
def _backend_keeps_conversations(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slack conversations stay on the backend unless a test says MDA holds or starts them."""
    if request.node.get_closest_marker("mda_lookups"):
        return
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(mda_forwarding, "startable_thread", AsyncMock(return_value=None))


@pytest.fixture(autouse=True)
def _no_btw_dm_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """No DM thread continues a `/btw` question unless a test says one does."""
    monkeypatch.setattr("openswe.slack.routes.continues_in_dm", AsyncMock(return_value=False))
    monkeypatch.setattr(
        "openswe.slack.webhook.ByTheWayFollowUp", SimpleNamespace(at=AsyncMock(return_value=None))
    )
