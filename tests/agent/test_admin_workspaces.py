from contextlib import ExitStack
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent import server
from agent.sandboxes import lifecycle
from agent.tools import workspaces as env_tools
from agent.users import User
from agent.utils.authorship import CollaboratorIdentity
from agent.workspaces.store import Workspace

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
    monkeypatch.setattr("agent.tools.access.langgraph_sdk.get_client", lambda: client)


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
    with patch("agent.run_config.get_config", return_value=config):
        assert (await env_tools.list_workspaces())["ok"] is False
        result = await env_tools.publish_workspace("base", "prompt")
        assert result["ok"] is False


# --- publish: capture this sandbox, then record ---


class _Publish:
    """Every seam `publish_workspace` crosses, patched, with the calls it made."""

    def __init__(self, existing: Workspace | None, saved: Workspace) -> None:
        self.existing = existing
        self.saved = saved
        self.calls: list[str] = []
        self.validate = AsyncMock()
        self.capture = AsyncMock(side_effect=self._capture)
        self.publish = AsyncMock(side_effect=self._publish)
        self.discard = AsyncMock()
        self.retire = AsyncMock()
        self.ensure_cron = AsyncMock(return_value="cron-1")
        self._stack = ExitStack()

    async def _capture(self, *_: object, **__: object) -> str:
        self.calls.append("capture")
        return "snap-new"

    async def _publish(self, *_: object, **__: object) -> Workspace:
        self.calls.append("write")
        return self.saved

    @property
    def definition(self) -> Any:
        assert self.publish.await_args is not None
        return self.publish.await_args.args[1]

    def __enter__(self) -> _Publish:
        backend = MagicMock()
        backend.id = "sb-thread"
        for target in (
            patch(
                "agent.run_config.get_config",
                return_value=_config(admin_thread=True, github_login="ramonn", thread_id="t-1"),
            ),
            patch.object(
                env_tools.store.WORKSPACES,
                "get",
                new_callable=AsyncMock,
                return_value=self.existing,
            ),
            patch(
                "agent.sandboxes.state.get_sandbox_backend",
                new_callable=AsyncMock,
                return_value=backend,
            ),
            patch("agent.sandboxes.state.unwrap_sandbox_backend", side_effect=lambda b: b),
            patch.object(env_tools.store.WORKSPACES, "assert_publishable", self.validate),
            patch.object(env_tools.store, "capture_sandbox_snapshot", self.capture),
            patch.object(env_tools.store.WORKSPACES, "publish", self.publish),
            patch.object(env_tools.store, "discard_unreferenced_snapshot", self.discard),
            patch.object(env_tools.store, "retire_superseded_snapshot", self.retire),
            patch.object(env_tools.refresh, "ensure_refresh_cron", self.ensure_cron),
        ):
            self._stack.enter_context(target)
        return self

    def __exit__(self, *exc: object) -> None:
        self._stack.close()


def _saved(**fields: Any) -> Workspace:
    return Workspace(
        slug="base", name="base", snapshot_status="ready", snapshot_id="snap-new", **fields
    )


@pytest.mark.asyncio
async def test_a_failed_capture_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with _Publish(existing=None, saved=_saved()) as seams:
        seams.capture.side_effect = RuntimeError("snapshot service unavailable")
        result = await env_tools.publish_workspace("base", "prompt")

    assert result["ok"] is False
    assert "snapshot service unavailable" in result["error"]
    seams.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_record_write_discards_the_orphaned_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The capture succeeded, the write did not: nothing is left half-written,
    and the image nothing points at is not left behind either."""
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with _Publish(existing=None, saved=_saved()) as seams:
        seams.publish.side_effect = RuntimeError("store unavailable")
        result = await env_tools.publish_workspace("base", "prompt")

    assert result["ok"] is False
    assert "store unavailable" in result["error"]
    seams.discard.assert_awaited_once_with("base", "snap-new")
    seams.retire.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_repository_another_workspace_owns_is_refused_before_the_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ownership and the min-one-repo rule are store rules, so they gate the capture too."""
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with _Publish(existing=None, saved=_saved()) as seams:
        seams.validate.side_effect = ValueError(
            "repository acme/api already belongs to workspace core"
        )
        result = await env_tools.publish_workspace("oss", "prompt", repos=["acme/api"])

    assert result == {"ok": False, "error": "repository acme/api already belongs to workspace core"}
    seams.capture.assert_not_awaited()
    seams.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_publishing_over_an_existing_workspace_retires_its_old_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    existing = Workspace(slug="base", name="base", snapshot_id="snap-old", snapshot_status="ready")
    with _Publish(existing=existing, saved=_saved()) as seams:
        result = await env_tools.publish_workspace("base", "prompt")

    assert seams.calls == ["capture", "write"]
    assert isinstance(seams.definition, env_tools.store.WorkspaceUpdate)
    # The old image goes only after the record points at the new one.
    seams.retire.assert_awaited_once_with("base", "snap-old", "snap-new")
    assert result["created"] is False


@pytest.mark.asyncio
async def test_publish_can_clear_sandbox_sizing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    with _Publish(existing=Workspace(slug="base"), saved=_saved(prompt="prompt")) as seams:
        result = await env_tools.publish_workspace(
            "base", "prompt", clear_sizing=True, clear_create_params=True
        )

    definition = seams.definition
    assert {"mem_bytes", "vcpus", "fs_capacity_bytes"} <= definition.model_fields_set
    assert definition.mem_bytes is None
    assert definition.vcpus is None
    assert definition.fs_capacity_bytes is None
    assert definition.create_params == {}
    assert "create_params" in definition.model_fields_set
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_refresh_start_refuses_while_one_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "ramonn")
    running = Workspace(
        slug="base",
        setup_script="make setup",
        refresh_status="refreshing",
        refresh_started_at=datetime.now(UTC).isoformat(),
        refresh_run_id="run-1",
    )
    start = AsyncMock()
    with (
        patch(
            "agent.run_config.get_config",
            return_value=_config(admin_thread=True, github_login="ramonn"),
        ),
        patch.object(
            env_tools.store.WORKSPACES, "get", new_callable=AsyncMock, return_value=running
        ),
        patch.object(env_tools.refresh, "start_refresh_run", start),
    ):
        result = await env_tools.refresh_workspace_start("base")

    assert result["status"] == "error"
    assert result["task_id"] == "ws-run-1"
    start.assert_not_awaited()


# --- prompt wiring ---


async def test_roster_admin_flag_is_the_participants_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """An admin requester must not make everyone else in the roster look like one."""
    from agent import server

    monkeypatch.setenv("CONFIGURED_ADMINS", "admin@example.com")
    monkeypatch.setattr(server, "_user_for_login", AsyncMock(return_value=None))
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
