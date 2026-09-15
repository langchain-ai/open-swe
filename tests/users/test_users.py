"""PostgreSQL regressions for users and their provider identities."""

import asyncio

import pytest
from sqlalchemy import func, select

from agent.database import postgres
from agent.users import User

pytestmark = pytest.mark.usefixtures("registry_db")


async def _user_count() -> int:
    async with postgres.session() as session:
        return await session.scalar(select(func.count()).select_from(User)) or 0


async def test_first_sign_in_creates_one_user_with_a_uuid7_id() -> None:
    signed_in = await User.sign_in(
        "github", "1001", login="OctoCat", email="octo@example.com", display_name="Octo Cat"
    )

    assert signed_in.id.version == 7
    assert [(i.provider, i.external_id, i.login, i.email) for i in signed_in.identities] == [
        ("github", "1001", "OctoCat", "octo@example.com")
    ]
    assert (signed_in.display_name, signed_in.is_admin) == ("Octo Cat", False)
    assert await _user_count() == 1


async def test_signing_in_again_returns_the_same_user() -> None:
    first = await User.sign_in("github", "1001", login="OctoCat")
    again = await User.sign_in("github", "1001", login="OctoCat")

    assert again.id == first.id
    assert await _user_count() == 1


async def test_known_values_win_and_empty_ones_keep_what_is_stored() -> None:
    await User.sign_in("github", "1001", login="OctoCat", email="octo@example.com")
    renamed = await User.sign_in("github", "1001", login="Octo-Cat", email="cat@example.com")
    unchanged = await User.sign_in("github", "1001")

    assert (renamed.identities[0].login, renamed.identities[0].email) == (
        "Octo-Cat",
        "cat@example.com",
    )
    assert (unchanged.identities[0].login, unchanged.identities[0].email) == (
        "Octo-Cat",
        "cat@example.com",
    )


async def test_admin_is_written_only_when_the_caller_has_an_opinion() -> None:
    promoted = await User.sign_in("github", "1001", login="OctoCat", admin=True)
    kept = await User.sign_in("github", "1001")
    demoted = await User.sign_in("github", "1001", admin=False)

    assert (promoted.is_admin, kept.is_admin, demoted.is_admin) == (True, True, False)


async def test_sync_admins_matches_github_logins_and_identity_emails_and_demotes() -> None:
    by_login = await User.sign_in("github", "1", login="OctoCat")
    by_email = await User.sign_in("github", "2", login="ada", email="Ada@Example.com")
    via_slack = await User.sign_in("github", "3", login="bob")
    await via_slack.link("slack", "U3", email="bob@example.com")
    stale = await User.sign_in("github", "4", login="carol", admin=True)

    changed = await User.sync_admins({"octocat", "ada@example.com", "bob@example.com"})

    assert changed == 4
    flags = {
        user.id: (reloaded.is_admin if (reloaded := await User.get(user.id)) else None)
        for user in (by_login, by_email, via_slack, stale)
    }
    assert flags == {by_login.id: True, by_email.id: True, via_slack.id: True, stale.id: False}
    assert await User.sync_admins({"octocat", "ada@example.com", "bob@example.com"}) == 0


async def test_linking_slack_reaches_the_same_person_from_either_side() -> None:
    github_user = await User.sign_in("github", "1001", login="OctoCat")
    linked = await github_user.link("slack", "U0123", login="octo", team_id="T9")

    assert [i.provider for i in linked.identities] == ["github", "slack"]
    from_slack = await User.for_identity("slack", "U0123")
    from_login = await User.for_login("github", "octocat")
    assert from_slack is not None and from_slack.id == github_user.id
    assert from_login is not None and from_login.id == github_user.id
    assert await _user_count() == 1


async def test_for_identity_is_none_for_an_unknown_account() -> None:
    await User.sign_in("github", "1001", login="OctoCat")

    assert await User.for_identity("slack", "U0123") is None


async def test_linking_a_claimed_identity_moves_it_to_the_new_owner() -> None:
    first = await User.sign_in("slack", "U0123", login="octo", team_id="T9")
    second = await User.sign_in("github", "1001", login="OctoCat")
    moved = await second.link("slack", "U0123")

    assert [i.provider for i in moved.identities] == ["github", "slack"]
    owner = await User.for_identity("slack", "U0123")
    assert owner is not None and owner.id == second.id
    reloaded_first = await User.get(first.id)
    assert reloaded_first is not None and reloaded_first.identities == []


async def test_concurrent_first_sign_ins_settle_on_one_user() -> None:
    signed_in = await asyncio.gather(
        *(User.sign_in("github", "1001", login="OctoCat") for _ in range(5))
    )

    assert len({user.id for user in signed_in}) == 1
    assert await _user_count() == 1
