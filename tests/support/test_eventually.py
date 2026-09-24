"""Tests for tests.support.eventually."""

import pytest

from tests.support import eventually as eventually_module
from tests.support.eventually import eventually


async def test_condition_that_first_holds_after_the_last_wait_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(eventually_module, "_INTERVAL_SECONDS", 0)
    checks = 0

    def holds_once_every_wait_is_over() -> bool:
        nonlocal checks
        checks += 1
        return checks > eventually_module._ATTEMPTS

    await eventually(holds_once_every_wait_is_over)
