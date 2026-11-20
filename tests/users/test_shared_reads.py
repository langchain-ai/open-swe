import asyncio
from unittest.mock import AsyncMock

import pytest

from openswe.users import User
from openswe.users.display_name_backfill import persist_display_name
from openswe.users.shared_reads import SharedUserReads

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _authorized_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


async def _ada() -> User:
    """Ada, signed in with GitHub and linked to Slack member ``U1``."""
    user = await User.sign_in("github", "1", login="ada")
    await user.link("slack", "U1")
    return user


async def test_a_scope_reads_each_person_once(monkeypatch: pytest.MonkeyPatch) -> None:
    ada = await _ada()
    read = AsyncMock(wraps=User._for_identity)
    monkeypatch.setattr(User, "_for_identity", read)

    async with SharedUserReads.scope():
        first, again, together = await asyncio.gather(
            User.for_identity("slack", "U1"),
            User.for_identity("slack", "U1"),
            User.for_identity("slack", "U1"),
        )
        async with SharedUserReads.scope():
            nested = await User.for_identity("slack", "U1")

    assert {user.id for user in (first, again, together, nested) if user} == {ada.id}
    read.assert_awaited_once()


async def test_outside_a_scope_every_lookup_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    await _ada()
    read = AsyncMock(wraps=User._for_identity)
    monkeypatch.setattr(User, "_for_identity", read)

    await User.for_identity("slack", "U1")
    await User.for_identity("slack", "U1")

    assert read.await_count == 2


async def test_a_lookup_in_the_callers_transaction_is_not_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller's transaction may hold writes the shared reads haven't seen."""
    read = AsyncMock(return_value=None)
    monkeypatch.setattr(User, "_for_identity", read)

    async with SharedUserReads.scope():
        await User.for_identity("slack", "U1")
        await User.for_identity("slack", "U1", session=AsyncMock())

    assert read.await_count == 2


async def test_a_write_to_a_person_is_seen_by_the_next_lookup() -> None:
    await _ada()

    async with SharedUserReads.scope():
        before = await User.for_identity("slack", "U1")
        await persist_display_name("U1", "Ada")
        after = await User.for_identity("slack", "U1")

    assert before is not None and before.display_name == ""
    assert after is not None and after.display_name == "Ada"


async def test_a_failed_lookup_is_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    ada = await _ada()
    read = AsyncMock(side_effect=[RuntimeError("database went away"), ada])
    monkeypatch.setattr(User, "_for_login", read)

    async with SharedUserReads.scope():
        with pytest.raises(RuntimeError):
            await User.for_login("github", "ada")
        assert await User.for_login("github", "Ada") is ada

    assert read.await_count == 2
