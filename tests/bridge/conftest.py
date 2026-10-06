import pytest

from agent.bridge import store


@pytest.fixture(autouse=True)
def _fresh_partition_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets a fresh schema, so a rotation another test ran says nothing about it."""
    monkeypatch.setattr(store, "_rotated_at", None)
