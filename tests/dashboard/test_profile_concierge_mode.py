"""PostgreSQL regressions for concierge mode on the dashboard profile endpoint."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from openswe.dashboard.personal_settings import patch_personal_settings
from openswe.dashboard.profiles import ProfileUpdate, put_my_profile
from openswe.slack.channels import SlackChannel
from openswe.users import User, UserPreferences
from openswe.users.records import UnknownUser
from openswe.workspaces.routing import is_kitchen_channel

pytestmark = pytest.mark.usefixtures("registry_db")

_SESSION = {"sub": "ada", "email": "ada@example.com"}


@pytest.fixture(autouse=True)
def _authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "ada")
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "")


def _update(concierge_mode: bool | None) -> ProfileUpdate:
    return ProfileUpdate(
        default_model="openai:gpt-6.1-sol", reasoning_effort="high", concierge_mode=concierge_mode
    )


async def test_saving_a_profile_needs_a_users_row() -> None:
    with pytest.raises(UnknownUser):
        await put_my_profile(_update(False), _SESSION)


async def test_turning_concierge_mode_on_needs_a_users_row() -> None:
    with pytest.raises(HTTPException) as exc:
        await put_my_profile(_update(True), _SESSION)
    assert exc.value.status_code == 409

    await User.sign_in("github", "1", login="ada")
    saved = await put_my_profile(_update(True), _SESSION)

    assert saved["concierge_mode"] is True
    assert await User.preferences_for_login("ada") == UserPreferences(concierge_mode=True)


@pytest.mark.parametrize("via_tool", [False, True])
async def test_personal_kitchen_validates_round_trips_and_clears(
    monkeypatch: pytest.MonkeyPatch, via_tool: bool
) -> None:
    person = await User.sign_in("github", "1", login="ada")
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
            await put_my_profile(ProfileUpdate(personal_kitchen_channel_id=channel.id), _SESSION)
        assert error.value.status_code == 422
    assert not await is_kitchen_channel(channel.id, slack_user_id="UADA")
    channel.payload = {"is_member": True, "is_ext_shared": False, "is_pending_ext_shared": False}
    if via_tool:
        await patch_personal_settings("ada", {"personal_kitchen_channel_id": " c0personal "})
    else:
        saved = await put_my_profile(
            ProfileUpdate(personal_kitchen_channel_id=" c0personal "), _SESSION
        )
        assert saved["personal_kitchen_channel_id"] == channel.id
    assert await is_kitchen_channel(channel.id, slack_user_id="UADA")
    assert not await is_kitchen_channel(channel.id, slack_user_id="UOTHER")
    assert not await is_kitchen_channel("C0ELSEWHERE", slack_user_id="UADA")
    await put_my_profile(ProfileUpdate(), _SESSION)
    assert await is_kitchen_channel(channel.id, slack_user_id="UADA")
    await patch_personal_settings("ada", {"personal_kitchen_channel_id": ""})
    assert not await is_kitchen_channel(channel.id, slack_user_id="UADA")
