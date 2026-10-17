"""Shared test setup."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt the suite out of analytics.

    Compiling an agent builds a transport and the suite compiles many. Tests
    that assert on emission inject a client instead.
    """
    monkeypatch.setenv("MDA_TELEMETRY_DISABLED", "1")
