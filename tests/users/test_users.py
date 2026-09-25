"""PostgreSQL regressions for users and their provider identities."""

import asyncio

import pytest
from sqlalchemy import func, select, update

from agent.database import postgres
from agent.experimental import ExperimentalFeaturesPatch
from agent.users import UnauthorizedUser, User, UserPreferences, UserPreferencesPatch

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _authorized_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "OctoCat,Octo-Cat,ada,bob,carol,Ada,renamed")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


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


async def test_an_existing_user_signs_in_without_re_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = await User.sign_in("github", "1001", login="OctoCat")
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "")

    again = await User.sign_in("github", "1001", login="OctoCat", display_name="Octo")

    assert (again.id, again.display_name) == (created.id, "Octo")


async def test_concurrent_first_sign_ins_settle_on_one_user() -> None:
    signed_in = await asyncio.gather(
        *(User.sign_in("github", "1001", login="OctoCat") for _ in range(5))
    )

    assert len({user.id for user in signed_in}) == 1
    assert await _user_count() == 1


async def test_the_work_email_wins_over_a_personal_github_one() -> None:
    """``resolve_run_email`` turns on this precedence: Slack's address is verified."""
    user = await User.sign_in("github", "1001", login="OctoCat", email="octo@personal.example")
    await user.link("slack", "U1", email="octo@work.example", team_id="T1")

    assert await User.email_for_login("OctoCat") == "octo@work.example"


async def test_the_github_email_is_used_when_no_slack_account_is_linked() -> None:
    await User.sign_in("github", "1001", login="OctoCat", email="octo@personal.example")

    assert await User.email_for_login("OctoCat") == "octo@personal.example"


async def test_concierge_mode_is_off_until_its_owner_turns_it_on() -> None:
    user = await User.sign_in("github", "7", login="ada")
    await user.link("slack", "U7")
    assert await User.concierge_mode_for_slack("U7") is False

    saved = await User.update_preferences("Ada", UserPreferencesPatch(concierge_mode=True))

    assert saved == UserPreferences(concierge_mode=True)
    assert await User.concierge_mode_for_slack("U7") is True
    assert await User.preferences_for_login("ada") == UserPreferences(concierge_mode=True)


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


async def test_experimental_flag_patches_keep_sibling_flags() -> None:
    await User.sign_in("github", "9", login="bob")
    async with postgres.session() as session:
        await session.execute(
            update(User).values(
                preferences={"concierge_mode": True, "experimental": {"future_flag": True}}
            )
        )

    await User.update_preferences(
        "bob",
        UserPreferencesPatch(experimental=ExperimentalFeaturesPatch(pr_comment_triggers=True)),
    )
    await User.default_preferences(
        "bob",
        UserPreferencesPatch(experimental=ExperimentalFeaturesPatch(pr_comment_triggers=False)),
    )

    stored = await User.for_login("github", "bob")
    assert stored is not None and stored.preferences == {
        "concierge_mode": True,
        "experimental": {"future_flag": True, "pr_comment_triggers": True},
    }


async def test_preferences_for_an_unknown_login_are_defaults_and_cannot_be_saved() -> None:
    assert await User.preferences_for_login("carol") == UserPreferences()
    assert await User.update_preferences("carol", UserPreferencesPatch(concierge_mode=True)) is None
    assert await User.concierge_mode_for_slack("U404") is False
