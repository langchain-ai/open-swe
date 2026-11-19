from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from openswe import server
from openswe.sandboxes import lifecycle
from openswe.slack.channels import SlackChannel
from openswe.tools import workspaces as env_tools
from openswe.users import User
from openswe.utils.authorship import CollaboratorIdentity
from openswe.workspaces.store import WORKSPACES, Workspace, WorkspaceCreate, WorkspaceRoutingUpdate

pytestmark = pytest.mark.usefixtures("registry_db")


_READY = Workspace(slug="base", name="Base", snapshot_status="ready", snapshot_id="env-snap")


def _config(**configurable: object) -> RunnableConfig:
    return cast(RunnableConfig, {"configurable": {"thread_id": "t-1", **configurable}})


@pytest.fixture(autouse=True)
def private_thread_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SimpleNamespace(
        threads=SimpleNamespace(
            get=AsyncMock(
                return_value={"metadata": {"visibility": "private", "owner_type": "user"}}
            )
        )
    )
    monkeypatch.setattr("openswe.utils.thread_reads.langgraph_sdk.get_client", lambda: client)


# --- snapshot precedence ---


@pytest.mark.asyncio
async def test_workspace_without_a_captured_snapshot_falls_back_to_the_provider_base() -> None:
    never_captured = _READY.model_copy(update={"snapshot_status": "failed", "snapshot_id": None})
    with patch.object(
        lifecycle, "load_workspace", new_callable=AsyncMock, return_value=never_captured
    ):
        assert (await lifecycle.SandboxCreateConfig.resolve()).snapshot_id is None


@pytest.mark.asyncio
async def test_a_nightly_capture_does_not_send_runs_to_the_base_image() -> None:
    """The new id lands only on success, so a refresh in flight changes nothing."""
    capturing = _READY.model_copy(update={"snapshot_status": "capturing"})
    with patch.object(lifecycle, "load_workspace", new_callable=AsyncMock, return_value=capturing):
        assert (await lifecycle.SandboxCreateConfig.resolve()).snapshot_id == "env-snap"


# --- admin thread gate ---


@pytest.mark.asyncio
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


# --- tool gate ---


@pytest.mark.asyncio
async def test_tools_refuse_non_admins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    config = _config(admin_thread=True, github_login="someone-else")
    with patch("openswe.run_config.get_config", return_value=config):
        assert (await env_tools.list_workspaces())["ok"] is False
        assert (await env_tools.snapshotctl("stop", "base"))["ok"] is False
        assert (await env_tools.read_execution_image())["ok"] is False
        assert (await env_tools.rebuild_execution_image())["ok"] is False
        assert (await env_tools.publish_execution_image())["ok"] is False
        assert (
            await env_tools.configure_workspace(
                "base", WorkspaceRoutingUpdate(kitchen_channel_ids=["C0NEW"])
            )
        )["ok"] is False


@pytest.mark.asyncio
async def test_snapshot_reads_need_a_private_admin_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Logs and audit history stay out of shared threads; writes still reach the store."""
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    shared = {"metadata": {"visibility": "public", "owner_type": "user", "admin_thread": True}}
    client = SimpleNamespace(threads=SimpleNamespace(get=AsyncMock(return_value=shared)))
    monkeypatch.setattr("openswe.utils.thread_reads.langgraph_sdk.get_client", lambda: client)
    get = AsyncMock(return_value=_READY)
    with (
        patch(
            "openswe.run_config.get_config",
            return_value=_config(admin_thread=True, github_login="ramonn"),
        ),
        patch.object(env_tools.store.WORKSPACES, "get", get),
    ):
        assert (await env_tools.snapshotctl("status", "base"))["ok"] is False
        assert (await env_tools.snapshotctl("history", "base"))["ok"] is False
        assert (await env_tools.read_execution_image())["ok"] is False
        global_task = await env_tools.refresh.task_status("image-not-visible")
        assert "private admin" in global_task["error"]
        get.assert_not_awaited()
        refused = await env_tools.snapshotctl("stop", "base")

    get.assert_awaited_once_with("base")
    assert "no refresh" in str(refused["error"])


# --- prompt wiring ---


async def test_roster_admin_flag_is_the_participants_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """An admin requester must not make everyone else in the roster look like one."""
    from openswe import server

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
async def test_configure_workspace_preserves_definition_and_checks_kitchen(
    monkeypatch: pytest.MonkeyPatch, registry_db: None, eligible: bool
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    await WORKSPACES.create(
        WorkspaceCreate(name="Core", prompt="keep me", slack_channel_ids=["C0OLD", "C0NEW"]),
        "ramonn",
    )
    channel = SlackChannel(
        id="C0NEW",
        payload={"is_member": eligible, "is_ext_shared": False, "is_pending_ext_shared": False},
    )
    config = _config(admin_thread=True, github_login="ramonn")
    with (
        patch("openswe.run_config.get_config", return_value=config),
        patch.object(SlackChannel, "load", AsyncMock(return_value=channel)),
    ):
        result = await env_tools.configure_workspace(
            "core", WorkspaceRoutingUpdate(kitchen_channel_ids=["C0NEW"])
        )
    assert result["ok"] is eligible
    stored = await WORKSPACES.get("core")
    assert stored is not None
    assert stored.prompt == "keep me"
    assert stored.slack_channel_ids == ["C0NEW", "C0OLD"]
    assert stored.kitchen_channel_ids == (["C0NEW"] if eligible else [])
