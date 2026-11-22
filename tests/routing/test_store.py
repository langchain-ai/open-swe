from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from openswe.database import postgres
from openswe.routing.store import INTEGRATIONS, RepositoryBinding, SlackChannelBinding
from openswe.slack.channels import SlackChannel
from openswe.users import User
from openswe.utils.json_types import JsonObject

pytestmark = pytest.mark.usefixtures("registry_db")


async def test_repository_binding_preserves_identity() -> None:
    async with postgres.session() as session:
        creator = User(display_name="Creator")
        editor = User(display_name="Editor")
        session.add_all([creator, editor])
    await INTEGRATIONS.configure_repository("Acme/Widgets", actor=creator.id)
    assert [repo.repo for repo in (await INTEGRATIONS.settings()).repos] == ["Acme/Widgets"]
    async with postgres.session() as session:
        before = (await session.scalars(select(RepositoryBinding))).one()
    await INTEGRATIONS.configure_repository("acme/widgets", actor=editor.id)
    async with postgres.session() as session:
        after = (await session.scalars(select(RepositoryBinding))).one()
    assert before.id == after.id
    assert after.created_by == creator.id
    assert after.updated_by == editor.id
    assert after.updated_at > before.updated_at
    await INTEGRATIONS.remove_repository("acme/widgets")
    await INTEGRATIONS.remove_repository("acme/widgets")
    assert (await INTEGRATIONS.settings()).repos == []
    await INTEGRATIONS.configure_repository("acme/widgets", actor=None)
    async with postgres.session() as session:
        replacement = (await session.scalars(select(RepositoryBinding))).one()
    assert replacement.id != before.id


@pytest.mark.parametrize("prompt", ["", "Investigate database reports; ask before making changes."])
@pytest.mark.parametrize("eligible", [True, False])
async def test_channel_prompt_checks_live_eligibility_before_writing(
    monkeypatch: pytest.MonkeyPatch, prompt: str, eligible: bool
) -> None:
    payload: JsonObject = {
        "id": "C0TEST",
        "is_member": eligible,
        "is_ext_shared": False,
        "is_pending_ext_shared": False,
    }
    await SlackChannel.save_all(payload)
    load = AsyncMock(return_value=SlackChannel(id="C0TEST", payload=payload))
    monkeypatch.setattr(SlackChannel, "load", load)
    if eligible:
        await INTEGRATIONS.configure_channel("c0test", prompt=prompt, actor=None)
        assert await INTEGRATIONS.channel_prompt("C0TEST") == prompt
        assert await INTEGRATIONS.listens_to_channel("C0TEST")
    else:
        with pytest.raises(ValueError, match="joined"):
            await INTEGRATIONS.configure_channel("C0TEST", prompt=prompt, actor=None)
        assert await INTEGRATIONS.channel_prompt("C0TEST") is None
    load.assert_awaited_once_with("C0TEST", use_cache=False, raise_unavailable=True)


async def test_channel_reconfiguration_keeps_one_binding_and_removal_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload: JsonObject = {
        "id": "C0TEST",
        "is_member": True,
        "is_ext_shared": False,
        "is_pending_ext_shared": False,
    }
    await SlackChannel.save_all(payload)
    monkeypatch.setattr(
        SlackChannel, "load", AsyncMock(return_value=SlackChannel(id="C0TEST", payload=payload))
    )
    await INTEGRATIONS.configure_channel("C0TEST", prompt="Investigate only", actor=None)
    async with postgres.session() as session:
        before = (await session.scalars(select(SlackChannelBinding))).one()
    await INTEGRATIONS.configure_channel("C0TEST", prompt="", actor=None)
    async with postgres.session() as session:
        after = (await session.scalars(select(SlackChannelBinding))).one()
    assert before.id == after.id
    assert await INTEGRATIONS.channel_prompt("C0TEST") == ""
    await INTEGRATIONS.remove_channel("C0TEST")
    await INTEGRATIONS.remove_channel("C0TEST")
    assert (await INTEGRATIONS.settings()).slack_channels == []
    assert not await INTEGRATIONS.listens_to_channel("C0TEST")
