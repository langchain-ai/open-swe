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

    resolved = await User.for_person({"id": "slack:U1"})

    assert resolved is not None and resolved.id == user.id


async def test_github_numeric_id_then_login_fallback() -> None:
    user = await User.sign_in("github", "1001", login="OctoCat")

    by_id = await User.for_person({"id": "github:1001"})
    by_login = await User.for_person({"id": "github:octocat"})
    by_field = await User.for_person({"id": "linear:abc", "github_login": "OctoCat"})

    assert by_id is not None and by_id.id == user.id
    assert by_login is not None and by_login.id == user.id
    assert by_field is not None and by_field.id == user.id


async def test_a_slack_account_nobody_linked_resolves_to_nobody() -> None:
    await User.sign_in("github", "1001", login="grace")

    assert await User.for_person({"id": "slack:U9", "display_name": "grace"}) is None


async def test_unknown_people_are_not_created() -> None:
    assert await User.for_person({"id": "slack:U404"}) is None
    assert await User.for_person({"id": "github:nobody"}) is None
    assert await User.for_person({"id": "dashboard"}) is None


async def test_canonical_person_keys_every_surface_on_one_person() -> None:
    user = await User.sign_in("github", "1001", login="OctoCat")
    await user.link("slack", "U1", team_id="T1")

    from_slack = await User.canonical_person({"id": "slack:U1"})
    from_dashboard = await User.canonical_person({"id": "github:octocat"})

    assert from_slack["id"] == from_dashboard["id"] == f"user:{user.id}"


async def test_canonical_person_only_rekeys(monkeypatch) -> None:
    """The run describes the person; resolution supplies nothing but the key."""
    user = await User.sign_in("github", "1001", login="OctoCat")
    await user.link("slack", "U1", team_id="T1")

    person = await User.canonical_person({"id": "slack:U1"})

    assert person == {"id": f"user:{user.id}"}


async def test_canonical_person_leaves_unknown_people_on_their_surface_id() -> None:
    person = await User.canonical_person({"id": "slack:U404"})

    assert person["id"] == "slack:U404"


async def test_canonical_person_survives_a_database_that_cannot_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatch runs on this path; a nicer key is never worth refusing a run."""

    async def unavailable(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("PostgreSQL is not configured")

    monkeypatch.setattr(User, "for_person", classmethod(unavailable))

    person = await User.canonical_person({"id": "github:octocat"})

    assert person["id"] == "github:octocat"
