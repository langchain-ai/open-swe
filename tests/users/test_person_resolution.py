"""PostgreSQL regressions for turning a PersonIdentity into a User."""

import pytest

from openswe.users import User

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _authorized_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "OctoCat,grace")


async def test_canonical_person_keys_every_surface_on_one_person() -> None:
    user = await User.sign_in("github", "1001", login="OctoCat")
    await user.link("slack", "U1", team_id="T1")

    from_slack = await User.canonical_person({"id": "slack:U1"})
    from_web = await User.canonical_person({"id": "github:octocat"})

    assert from_slack["id"] == from_web["id"] == f"user:{user.id}"


async def test_canonical_person_survives_a_database_that_cannot_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatch runs on this path; a nicer key is never worth refusing a run."""

    async def unavailable(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("PostgreSQL is not configured")

    monkeypatch.setattr(User, "for_person", classmethod(unavailable))

    person = await User.canonical_person({"id": "github:octocat"})

    assert person["id"] == "github:octocat"
