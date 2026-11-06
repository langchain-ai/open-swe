"""Shared fixtures for the Slack tests."""

from unittest.mock import AsyncMock

import pytest

from openswe.slack import forks, mda_forwarding


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "mda_lookups: use MDA forwarding's real thread lookups")
    config.addinivalue_line("markers", "real_forks: fork threads with the real Agent Server calls")


@pytest.fixture(autouse=True)
def _backend_keeps_conversations(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slack conversations stay on the backend unless a test says MDA holds or starts them."""
    if request.node.get_closest_marker("mda_lookups"):
        return
    monkeypatch.setattr(mda_forwarding, "owned_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(mda_forwarding, "startable_thread", AsyncMock(return_value=None))
    monkeypatch.setattr(mda_forwarding, "startable_concierge", AsyncMock(return_value=None))


@pytest.fixture(autouse=True)
def _branches_seed_from_slack(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/btw` and breakouts are seeded from Slack unless a test branches their thread."""
    if request.node.get_closest_marker("real_forks"):
        return
    monkeypatch.setattr(forks, "branches_from", AsyncMock(return_value=False))
    monkeypatch.setattr(forks, "held_timestamps", AsyncMock(return_value=set()))
