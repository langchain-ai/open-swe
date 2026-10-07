"""PostgreSQL regressions for users and their provider identities."""

import asyncio

import pytest
from sqlalchemy import func, select, update

from openswe.database import postgres
from openswe.users import UnauthorizedUser, User, UserPreferences, UserPreferencesPatch

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _authorized_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "OctoCat,Octo-Cat,ada,bob,carol,Ada,renamed")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


async def _user_count() -> int:
    async with postgres.session() as session:
        return await session.scalar(select(func.count()).select_from(User)) or 0


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


async def test_user_search_filters_before_pagination_without_duplicate_identities() -> None:
    await User.sign_in("github", "1", login="bob")
    first = await User.sign_in("github", "2", login="ada", email="team@example.com")
    await first.link("slack", "U_FIRST", login="team", email="team@example.com")
    second = await User.sign_in("github", "3", login="carol", display_name="Team 100%_complete")

    for offset, expected in enumerate((first, second)):
        users, total = await User.page(offset=offset, limit=1, search=" TEAM ")
        assert total == 2
        assert [user.id for user in users] == [expected.id]

    for search, expected in (
        ("ADA", first),
        ("EXAMPLE.COM", first),
        ("u_first", first),
        ("%_", second),
    ):
        users, total = await User.page(offset=0, limit=10, search=search)
        assert total == 1
        assert [user.id for user in users] == [expected.id]


async def test_linking_slack_reaches_the_same_person_from_either_side() -> None:
    github_user = await User.sign_in("github", "1001", login="OctoCat")
    linked = await github_user.link("slack", "U0123", login="octo", team_id="T9")

    assert [i.provider for i in linked.identities] == ["github", "slack"]
    from_slack = await User.for_identity("slack", "U0123")
    from_login = await User.for_login("github", "octocat")
    assert from_slack is not None and from_slack.id == github_user.id
    assert from_login is not None and from_login.id == github_user.id
    assert await _user_count() == 1


async def test_linking_a_claimed_identity_moves_it_to_the_new_owner() -> None:
    first = await User.sign_in("github", "2002", login="ada")
    await first.link("slack", "U0123", team_id="T9")
    second = await User.sign_in("github", "1001", login="OctoCat")
    moved = await second.link("slack", "U0123")

    assert [i.provider for i in moved.identities] == ["github", "slack"]
    owner = await User.for_identity("slack", "U0123")
    assert owner is not None and owner.id == second.id
    reloaded_first = await User.get(first.id)
    assert reloaded_first is not None
    assert [i.provider for i in reloaded_first.identities] == ["github"]


async def test_an_unauthorized_github_login_gets_no_user_row() -> None:
    with pytest.raises(UnauthorizedUser):
        await User.sign_in("github", "666", login="outsider")

    assert await _user_count() == 0
    assert await User.for_identity("github", "666") is None


async def test_a_slack_account_cannot_establish_a_user_on_its_own() -> None:
    with pytest.raises(UnauthorizedUser):
        await User.sign_in("slack", "U0123", login="octo", team_id="T9")

    assert await _user_count() == 0


async def test_concurrent_first_sign_ins_settle_on_one_user() -> None:
    signed_in = await asyncio.gather(
        *(User.sign_in("github", "1001", login="OctoCat") for _ in range(5))
    )

    assert len({user.id for user in signed_in}) == 1
    assert await _user_count() == 1


async def test_preference_patches_merge_and_skip_unset_fields() -> None:
    await User.sign_in("github", "8", login="bob")
    async with postgres.session() as session:
        await session.execute(
            update(User).values(preferences={"concierge_mode": True, "future": "kept"})
        )

    unchanged = await User.update_preferences("bob", UserPreferencesPatch())
    turned_off = await User.update_preferences("bob", UserPreferencesPatch(concierge_mode=False))

    assert unchanged == UserPreferences(concierge_mode=True)
    assert turned_off == UserPreferences(concierge_mode=False)
    stored = await User.for_login("github", "bob")
    assert stored is not None and stored.preferences == {"concierge_mode": False, "future": "kept"}
