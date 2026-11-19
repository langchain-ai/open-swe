"""PostgreSQL regressions for concierge mode on the dashboard profile endpoint."""

from contextlib import AsyncExitStack
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.dashboard.deps import DashboardRead
from openswe.dashboard.personal_settings import patch_personal_settings
from openswe.dashboard.principal import AuthorizedClaims, DashboardPrincipal
from openswe.dashboard.profiles import ProfileUpdate, put_my_profile
from openswe.database import postgres
from openswe.jobs import queue
from openswe.routing.store import is_kitchen_channel
from openswe.slack.channels import PERSONAL_KITCHEN_JOB, SlackChannel
from openswe.users import User, UserPreferences

pytestmark = pytest.mark.usefixtures("registry_db")

_SESSION = {"sub": "ada", "email": "ada@example.com"}


@pytest.fixture(autouse=True)
def _authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


def _update(concierge_mode: bool | None) -> ProfileUpdate:
    return ProfileUpdate(concierge_mode=concierge_mode)


class ProfileRequests:
    def __init__(self, person: User) -> None:
        self.person = person

    async def put(self, update: ProfileUpdate) -> dict[str, object]:
        async with AsyncExitStack() as scope:
            db = await scope.enter_async_context(postgres.session())
            person = await User.get(self.person.id, session=db)
            assert person is not None
            reader = DashboardRead(
                DashboardPrincipal.from_session(_SESSION),
                person,
                AuthorizedClaims(_SESSION, person),
                "ada@example.com",
                db,
                scope,
            )
            return await put_my_profile(update, reader)


@pytest.fixture
async def reader():
    person = await User.sign_in("github", "1", login="ada")
    async with AsyncExitStack() as scope:
        db = await scope.enter_async_context(postgres.session())
        yield DashboardRead(
            DashboardPrincipal.from_session(_SESSION),
            person,
            AuthorizedClaims(_SESSION, person),
            "ada@example.com",
            db,
            scope,
        )


async def test_turning_concierge_mode_on(reader) -> None:
    saved = await put_my_profile(_update(True), reader)
    assert saved["concierge_mode"] is True
    await reader.close()
    assert await User.preferences_for_login("ada") == UserPreferences(concierge_mode=True)


@pytest.mark.parametrize("via_tool", [False, True])
async def test_personal_kitchen_validates_round_trips_and_clears(
    monkeypatch: pytest.MonkeyPatch, via_tool: bool, reader: DashboardRead
) -> None:
    requests = ProfileRequests(reader.user)
    person = reader.user
    await reader.close()
    with pytest.raises(ValueError, match="Link your Slack"):
        await patch_personal_settings("ada", {"personal_kitchen_channel_id": "C0PERSONAL"})
    await person.link("slack", "UADA")
    channel = SlackChannel(id="C0PERSONAL", payload={"is_member": False})
    monkeypatch.setattr(SlackChannel, "load", AsyncMock(return_value=channel))
    if via_tool:
        with pytest.raises(ValueError, match="internal Slack channel"):
            await patch_personal_settings("ada", {"personal_kitchen_channel_id": channel.id})
    else:
        with pytest.raises(HTTPException) as error:
            await requests.put(ProfileUpdate(personal_kitchen_channel_id=channel.id))
        assert error.value.status_code == 422
    assert not await is_kitchen_channel(channel.id, slack_user_id="UADA")
    channel.payload = {"is_member": True, "is_ext_shared": False, "is_pending_ext_shared": False}
    for creator in ("", "UOTHER"):
        channel.payload["creator"] = creator
        if via_tool:
            with pytest.raises(ValueError, match="you created"):
                await patch_personal_settings("ada", {"personal_kitchen_channel_id": channel.id})
        else:
            with pytest.raises(HTTPException) as error:
                await requests.put(ProfileUpdate(personal_kitchen_channel_id=channel.id))
            assert error.value.status_code == 422
    channel.payload["creator"] = "UADA"
    channel.payload["purpose"] = {"value": "x" * 250}
    with pytest.raises(ValueError, match="250 characters"):
        await patch_personal_settings("ada", {"personal_kitchen_channel_id": channel.id})
    assert await queue.claim_due(10) == []
    channel.payload["purpose"] = {"value": "Repository: langchain-ai/deepagents"}
    if via_tool:
        await patch_personal_settings("ada", {"personal_kitchen_channel_id": " c0personal "})
    else:
        saved = await requests.put(ProfileUpdate(personal_kitchen_channel_id=" c0personal "))
        assert saved["personal_kitchen_channel_id"] == channel.id
    assert await is_kitchen_channel(channel.id, slack_user_id="UADA")
    assert not await is_kitchen_channel(channel.id, slack_user_id="UOTHER")
    assert not await is_kitchen_channel("C0ELSEWHERE", slack_user_id="UADA")
    [label] = await queue.claim_due(10)
    assert label.kind == PERSONAL_KITCHEN_JOB
    assert label.payload == {"channel_id": channel.id}
    await label.finish()
    await requests.put(ProfileUpdate())
    assert await is_kitchen_channel(channel.id, slack_user_id="UADA")
    assert await queue.claim_due(10) == []
    elsewhere = SlackChannel(id="C0ELSEWHERE", payload=channel.payload)
    monkeypatch.setattr(SlackChannel, "load", AsyncMock(return_value=elsewhere))
    await patch_personal_settings("ada", {"personal_kitchen_channel_id": elsewhere.id})
    assert await is_kitchen_channel("C0ELSEWHERE", slack_user_id="UADA")
    relabel = await queue.claim_due(10)
    assert {job.payload["channel_id"] for job in relabel} == {channel.id, "C0ELSEWHERE"}
    for job in relabel:
        await job.finish()
    await patch_personal_settings("ada", {"personal_kitchen_channel_id": ""})
    assert not await is_kitchen_channel("C0ELSEWHERE", slack_user_id="UADA")
    [cleanup] = await queue.claim_due(10)
    assert cleanup.payload == {"channel_id": "C0ELSEWHERE"}
