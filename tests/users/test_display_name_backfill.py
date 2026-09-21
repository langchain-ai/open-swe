"""Unit tests for filling in display names from Slack profiles."""

from unittest.mock import AsyncMock

import pytest

from agent.users import User, persist_display_name

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _authorized_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "OctoCat")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


async def _sign_in_named(display_name: str = "") -> User:
    user = await User.sign_in("github", "1001", login="OctoCat")
    await user.link("slack", "U1")
    if display_name:
        await user.rename(display_name)
    return user


async def test_fills_an_empty_display_name_from_slack(monkeypatch: pytest.MonkeyPatch) -> None:
    await _sign_in_named()
    link = AsyncMock()
    monkeypatch.setattr(User, "link", link)

    await persist_display_name("U1", "Octo Cat")

    assert link.await_count == 1
    renamed = await User.for_identity("github", "1001")
    assert renamed is not None and renamed.display_name == "Octo Cat"


async def test_keeps_an_existing_display_name(monkeypatch: pytest.MonkeyPatch) -> None:
    await _sign_in_named("GitHub Claimed Name")
    link = AsyncMock()
    monkeypatch.setattr(User, "link", link)

    await persist_display_name("U1", "Slack Name")

    link.assert_not_awaited()
    renamed = await User.for_identity("github", "1001")
    assert renamed is not None and renamed.display_name == "GitHub Claimed Name"


@pytest.mark.parametrize("name", ["", "   ", "unknown", None])
async def test_an_empty_name_writes_nothing(
    name: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _sign_in_named()
    link = AsyncMock()
    monkeypatch.setattr(User, "link", link)

    await persist_display_name("U1", name or "")

    link.assert_not_awaited()


@pytest.mark.parametrize("slack_user_id", ["", "U-MISSING"])
async def test_an_unknown_person_writes_nothing(slack_user_id: str) -> None:
    await persist_display_name(slack_user_id, "Octo Cat")
