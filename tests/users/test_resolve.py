"""PostgreSQL regressions for turning a PersonIdentity into a User."""

from unittest.mock import AsyncMock

import pytest

from agent.users import User, resolve
from agent.users.resolve import resolve_person

pytestmark = pytest.mark.usefixtures("registry_db")


@pytest.fixture(autouse=True)
def _no_legacy_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolve, "login_for_slack_id", AsyncMock(return_value=None))


async def test_slack_identity_resolves_to_its_person() -> None:
    user = await User.sign_in("slack", "U1", team_id="T1")

    resolved = await resolve_person({"id": "slack:U1", "platform": "slack"})

    assert resolved is not None and resolved.id == user.id


async def test_github_numeric_id_then_login_fallback() -> None:
    user = await User.sign_in("github", "1001", login="OctoCat")

    by_id = await resolve_person({"id": "github:1001", "platform": "github"})
    by_login = await resolve_person({"id": "github:octocat", "platform": "github"})
    by_field = await resolve_person({"id": "linear:abc", "github_login": "OctoCat"})

    assert by_id is not None and by_id.id == user.id
    assert by_login is not None and by_login.id == user.id
    assert by_field is not None and by_field.id == user.id


async def test_legacy_slack_mapping_links_the_slack_identity_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await User.sign_in("github", "1001", login="grace")
    legacy = AsyncMock(return_value="grace")
    monkeypatch.setattr(resolve, "login_for_slack_id", legacy)

    first = await resolve_person({"id": "slack:U9", "platform": "slack", "handle": "grace.h"})
    second = await resolve_person({"id": "slack:U9", "platform": "slack"})

    assert first is not None and first.id == user.id
    assert second is not None and second.id == user.id
    assert legacy.await_count == 1
    assert [(i.provider, i.external_id, i.login) for i in second.identities] == [
        ("github", "1001", "grace"),
        ("slack", "U9", "grace.h"),
    ]


async def test_unknown_people_are_not_created() -> None:
    assert await resolve_person({"id": "slack:U404", "platform": "slack"}) is None
    assert await resolve_person({"id": "github:nobody", "platform": "github"}) is None
    assert await resolve_person({"id": "dashboard", "platform": "dashboard"}) is None
