"""Shared fixtures for the Slack tests."""

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
