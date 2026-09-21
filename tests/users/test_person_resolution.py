"""PostgreSQL regressions for turning a PersonIdentity into a User."""

import pytest

from agent.users import User

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _authorized_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "OctoCat,grace")


async def test_slack_identity_resolves_to_its_person() -> None:
    user = await User.sign_in("github", "1001", login="OctoCat")
    await user.link("slack", "U1", team_id="T1")

    resolved = await User.for_person({"id": "slack:U1", "platform": "slack"})

    assert resolved is not None and resolved.id == user.id


async def test_github_numeric_id_then_login_fallback() -> None:
    user = await User.sign_in("github", "1001", login="OctoCat")

    by_id = await User.for_person({"id": "github:1001", "platform": "github"})
    by_login = await User.for_person({"id": "github:octocat", "platform": "github"})
    by_field = await User.for_person({"id": "linear:abc", "github_login": "OctoCat"})

    assert by_id is not None and by_id.id == user.id
    assert by_login is not None and by_login.id == user.id
    assert by_field is not None and by_field.id == user.id


async def test_a_slack_account_nobody_linked_resolves_to_nobody() -> None:
    await User.sign_in("github", "1001", login="grace")

    assert await User.for_person({"id": "slack:U9", "platform": "slack", "handle": "grace"}) is None


async def test_unknown_people_are_not_created() -> None:
    assert await User.for_person({"id": "slack:U404", "platform": "slack"}) is None
    assert await User.for_person({"id": "github:nobody", "platform": "github"}) is None
    assert await User.for_person({"id": "dashboard", "platform": "dashboard"}) is None


async def test_canonical_person_keys_every_surface_on_one_person() -> None:
    user = await User.sign_in("github", "1001", login="OctoCat")
    await user.link("slack", "U1", team_id="T1")

    from_slack = await User.canonical_person({"id": "slack:U1", "platform": "slack"})
    from_dashboard = await User.canonical_person({"id": "github:octocat", "platform": "github"})

    assert from_slack["id"] == from_dashboard["id"] == f"user:{user.id}"
    assert from_slack["platform"] == "slack"
    assert from_dashboard["platform"] == "github"
    assert from_slack["github_login"] == "OctoCat"


async def test_canonical_person_keeps_the_name_the_surface_gave(monkeypatch) -> None:
    """Only the key changes: display fields are what a transcript renders."""
    user = await User.sign_in("github", "1001", login="OctoCat")
    await user.link("slack", "U1", team_id="T1")

    person = await User.canonical_person({"id": "slack:U1", "platform": "slack"})

    assert "display_name" not in person


async def test_canonical_person_leaves_unknown_people_on_their_surface_id() -> None:
    person = await User.canonical_person({"id": "slack:U404", "platform": "slack"})

    assert person["id"] == "slack:U404"
