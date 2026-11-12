"""Incident channel settings."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from openswe.database import postgres
from openswe.incidents import service
from openswe.incidents.models import IncidentPolicy
from openswe.incidents.records import IncidentPolicyRow
from openswe.slack.channels import SlackChannel
from openswe.users import User

CHANNEL = {
    "id": "C1",
    "name": "inc-api",
    "is_channel": True,
    "is_member": True,
    "is_private": False,
    "is_ext_shared": False,
    "is_pending_ext_shared": False,
}


@pytest.fixture
async def configured(registry_db, monkeypatch):
    await service.POLICIES.put(
        IncidentPolicy(enabled=True, workspace_id="T1", slack_app_id="A1", enabled_at=100),
    )
    monkeypatch.setattr(SlackChannel, "fetch", AsyncMock(return_value=dict(CHANNEL)))


def _auth(monkeypatch, team="T1", scopes=None):
    monkeypatch.setattr(
        service,
        "_auth_test",
        AsyncMock(
            return_value=(
                {"team_id": team},
                list(service.REQUIRED_SLACK_SCOPES) if scopes is None else scopes,
            )
        ),
    )
    return service._auth_test


async def test_settings_record_the_person_who_wrote_them(configured, monkeypatch):
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "admin")
    admin = await User.sign_in("github", "1", login="admin")
    _auth(monkeypatch, "T1")
    monkeypatch.setenv("SLACK_APP_ID", "A1")
    await service.update_settings(
        {"enabled": True}, 0, {"id": "github:admin", "github_login": "admin"}
    )

    async with postgres.session() as session:
        policy = await session.execute(
            select(IncidentPolicyRow.created_by, IncidentPolicyRow.updated_by)
        )
    # The fixture's policy was a system write; the admin only updated it.
    assert policy.tuples().one() == (None, admin.id)


def test_channels_need_the_prefix_and_must_be_public_and_included():
    policy = IncidentPolicy(channel_prefix="inc-", excluded_channel_ids=["C9"])
    channel = {**CHANNEL, "id": "C7", "name": "payments-oncall"}
    assert service.channel_allowed(channel, policy) is False
    assert service.channel_allowed(channel, policy, require_prefix=False) is True
    assert service.channel_allowed({**channel, "id": "C9"}, policy, require_prefix=False) is False
    assert (
        service.channel_allowed({**channel, "is_private": True}, policy, require_prefix=False)
        is False
    )
