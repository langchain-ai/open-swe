"""The proxy config MDA writes, as the backend hands it over, and when it asks again."""

from datetime import UTC, datetime, timedelta

import pytest
from rt.managed_sandbox import SandboxProxyWriter


def _holding() -> SandboxProxyWriter:
    """A writer that holds the config the backend handed over."""
    writer = SandboxProxyWriter()
    writer._backend_config = {"rules": [{"name": "github-api"}, {"name": "open-swe-thread-tools"}]}
    return writer


def test_the_config_is_written_as_the_backend_handed_it_over() -> None:
    assert _holding().config() == {
        "rules": [{"name": "github-api"}, {"name": "open-swe-thread-tools"}]
    }
    assert SandboxProxyWriter().config() is None


@pytest.mark.parametrize(
    ("minutes_left", "stale"), [(None, False), (60, False), (16, False), (14, True), (-1, True)]
)
def test_the_config_is_asked_for_again_before_its_token_expires(
    minutes_left: int | None, stale: bool
) -> None:
    writer = _holding()
    writer._backend_expires_at = (
        None if minutes_left is None else datetime.now(UTC) + timedelta(minutes=minutes_left)
    )

    assert writer._stale() is stale


def test_a_replaced_sandbox_forgets_the_config_and_its_token() -> None:
    writer = _holding()
    writer._backend_expires_at = datetime.now(UTC)

    writer.replaced()

    assert writer.config() is None
    assert not writer._stale()


async def test_rewriting_after_a_refusal_asks_for_a_newly_minted_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = _holding()
    asked: list[bool] = []

    async def write(runtime: object, sandbox_id: str | None = None) -> None:
        asked.append(writer._force_refresh)
        writer._backend_config = {"rules": []}
        writer._backend_expires_at = datetime.now(UTC) + timedelta(hours=1)

    monkeypatch.setattr(writer, "write", write)

    assert await writer.rewrite_fresh(object()) is True
    assert asked == [True]
    assert writer._force_refresh is False
