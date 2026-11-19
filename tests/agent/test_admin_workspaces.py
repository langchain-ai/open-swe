from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from openswe import server
from openswe.execution_images import EXECUTION_IMAGES
from openswe.routing import tools as routing_tools
from openswe.routing.store import INTEGRATIONS
from openswe.slack.channels import SlackChannel
from openswe.tools.snapshotctl import (
    publish_execution_image,
    read_execution_image,
    rebuild_execution_image,
    snapshotctl,
)
from openswe.users import User
from openswe.utils.authorship import CollaboratorIdentity
from tests.conftest import serve_threads_from

pytestmark = pytest.mark.usefixtures("registry_db")


def _config(**configurable: object) -> RunnableConfig:
    return cast(RunnableConfig, {"configurable": {"thread_id": "t-1", **configurable}})


@pytest.fixture(autouse=True)
def private_thread_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    serve_threads_from(
        monkeypatch,
        AsyncMock(return_value={"metadata": {"visibility": "private", "owner_type": "user"}}),
    )


async def test_admin_thread_accepts_configured_admin_slack_dm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    config = _config(
        admin_thread=True,
        source="slack",
        github_login="ramonn",
        slack_thread={
            "channel_id": "D123",
            "thread_ts": "1700000000.000100",
            "channel_context": {"is_im": True},
        },
    )

    assert await server._admin_thread(config, None) is True


async def test_tools_refuse_non_admins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    config = _config(admin_thread=True, github_login="someone-else")
    with patch("openswe.run_config.get_config", return_value=config):
        assert (await routing_tools.list_integration_bindings())["ok"] is False
        assert (await routing_tools.bind_repository("acme/app"))["ok"] is False
        assert (await routing_tools.unbind_repository("acme/app"))["ok"] is False
        assert (await routing_tools.bind_slack_channel("C0NEW", "kitchen"))["ok"] is False
        assert (await routing_tools.unbind_slack_channel("C0NEW"))["ok"] is False
        assert (await snapshotctl("stop"))["ok"] is False
        assert (await read_execution_image())["ok"] is False
        assert (await rebuild_execution_image())["ok"] is False
        assert (await publish_execution_image())["ok"] is False


async def test_snapshot_reads_need_a_private_admin_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    serve_threads_from(
        monkeypatch,
        AsyncMock(
            return_value={
                "metadata": {"visibility": "public", "owner_type": "user", "admin_thread": True}
            }
        ),
    )
    status = AsyncMock(return_value={"status": "idle"})
    with (
        patch(
            "openswe.run_config.get_config",
            return_value=_config(admin_thread=True, github_login="ramonn"),
        ),
        patch.object(EXECUTION_IMAGES, "status", status),
    ):
        assert (await snapshotctl("status"))["ok"] is False
        assert (await snapshotctl("history"))["ok"] is False
        assert (await read_execution_image())["ok"] is False
        status.assert_not_awaited()
        refused = await snapshotctl("stop")

    assert "no global execution image build" in str(refused["error"])


async def test_roster_admin_flag_is_the_participants_own(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin@example.com")
    monkeypatch.setattr(server, "_participant_user", AsyncMock(return_value=None))
    monkeypatch.setattr(server, "load_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(server, "_resolve_user_custom_instructions", AsyncMock(return_value=None))
    monkeypatch.setattr(User, "email_for_login", AsyncMock(return_value="bob@example.com"))
    bob = CollaboratorIdentity(
        display_name="bob", commit_name="bob", commit_email="bob@example.com", github_login="bob"
    )
    admin_requester_config = _config(user_email="admin@example.com")

    as_seen_by_admin = await server._thread_participant(bob, admin_requester_config)
    assert as_seen_by_admin.workspace_admin is False

    monkeypatch.setenv("CONFIGURED_ADMINS", "admin@example.com,bob")
    assert (await server._thread_participant(bob, admin_requester_config)).workspace_admin is True


@pytest.mark.parametrize("eligible", [True, False])
async def test_channel_binding_checks_kitchen_eligibility(
    monkeypatch: pytest.MonkeyPatch, eligible: bool
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    await SlackChannel.save_all({"id": "C0NEW", "name": "new"})
    channel = SlackChannel(
        id="C0NEW",
        payload={"is_member": eligible, "is_ext_shared": False, "is_pending_ext_shared": False},
    )
    config = _config(admin_thread=True, github_login="ramonn")
    with (
        patch("openswe.run_config.get_config", return_value=config),
        patch.object(SlackChannel, "load", AsyncMock(return_value=channel)),
    ):
        if eligible:
            result = await routing_tools.bind_slack_channel("C0NEW", "kitchen")
            assert result["ok"] is True
        else:
            with pytest.raises(ValueError, match="internal channel"):
                await routing_tools.bind_slack_channel("C0NEW", "kitchen")
    assert await INTEGRATIONS.channel_mode("C0NEW") == ("kitchen" if eligible else None)
