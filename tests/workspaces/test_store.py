from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.workspaces import store as env_store
from agent.workspaces.store import (
    WORKSPACES,
    Workspace,
    WorkspaceCreate,
    WorkspaceUpdate,
)

# --- slug + snapshot naming (sync) ---


@pytest.mark.parametrize(
    "create_params",
    [
        {"env_vars": {"API_TOKEN": "sensitive"}},
        {"env_vars": {"OPENAI_API_KEY": "sensitive"}},
        {"clientSecret": "sensitive"},
        {
            "proxy_config": {
                "rules": [
                    {"headers": [{"name": "Authorization", "type": "opaque", "value": "sensitive"}]}
                ]
            }
        },
        {
            "proxy_config": {
                "rules": [{"headers": [{"name": "X-OpenAI-Api-Key", "value": "sensitive"}]}]
            }
        },
    ],
)
def test_create_params_reject_persisted_secrets(create_params: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="must not contain secrets"):
        WorkspaceCreate(name="env", create_params=create_params)


def test_create_params_enforce_serialized_size_limit() -> None:
    with pytest.raises(ValueError, match="at most"):
        WorkspaceCreate(
            name="env",
            create_params={"metadata": "x" * env_store.CREATE_PARAMS_MAX_CHARS},
        )


# --- CRUD (PostgreSQL) ---


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_rename_preserves_workspace_identity_and_snapshot() -> None:
    await WORKSPACES.create(
        WorkspaceCreate(name="draft", repos=["acme/draft"], slack_channel_ids=["C123"]),
        "ramon",
    )
    await WORKSPACES.mark_captured(
        "draft",
        snapshot_id="snap-1",
        snapshot_name="draft-image",
        source_sandbox_id="sb-1",
    )

    updated = await WORKSPACES.apply_update("draft", WorkspaceUpdate(name="New name"))
    persisted = await WORKSPACES.get("draft")

    assert persisted == updated
    assert updated.name == "New name"
    assert updated.slug == "draft"
    assert updated.repos == ["acme/draft"]
    assert updated.slack_channel_ids == ["C123"]
    assert updated.ready_snapshot_id == "snap-1"
    assert updated.snapshot_name == "draft-image"
    assert await WORKSPACES.get("new-name") is None
    edited = await WORKSPACES.apply_update("draft", WorkspaceUpdate(name="New name", prompt="new"))
    assert edited.prompt == "new"


_STATE_WRITES = {
    "mark_refreshing": lambda slug: WORKSPACES.mark_refreshing(slug),
    "start_refresh_step": lambda slug: WORKSPACES.start_refresh_step(slug, "boot"),
    "finish_refresh_step": lambda slug: WORKSPACES.finish_refresh_step(slug, "boot", "success"),
    "mark_refresh_builder": lambda slug: WORKSPACES.mark_refresh_builder(slug, "sb-1"),
    "mark_refresh_settled": lambda slug: WORKSPACES.mark_refresh_settled(slug, "success"),
    "mark_capturing": lambda slug: WORKSPACES.mark_capturing(slug),
    "mark_capture_settled": lambda slug: WORKSPACES.mark_capture_settled(slug, "failed", "boom"),
    "mark_captured": lambda slug: WORKSPACES.mark_captured(
        slug, snapshot_id="snap-2", snapshot_name="core-image", source_sandbox_id="sb-2"
    ),
    "set_refresh_run_id": lambda slug: WORKSPACES.set_refresh_run_id(slug, "run-1"),
    "set_refresh_cron_id": lambda slug: WORKSPACES.set_refresh_cron_id(slug, "cron-1"),
}


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
@pytest.mark.parametrize("write", sorted(_STATE_WRITES))
async def test_refresh_state_writes_never_revert_a_definition_edit(write: str) -> None:
    await WORKSPACES.create(WorkspaceCreate(name="Core", repos=["acme/api"]), "ramon")
    # What a refresh read before an admin edited the workspace.
    stale = await WORKSPACES.get("core")
    await WORKSPACES.apply_update("core", WorkspaceUpdate(repos=["acme/web"], prompt="new"))

    with patch.object(WORKSPACES, "get", AsyncMock(return_value=stale)):
        await _STATE_WRITES[write]("core")

    stored = await WORKSPACES.get("core")
    assert stored is not None
    assert stored.repos == ["acme/web"]
    assert stored.prompt == "new"


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_a_definition_edit_never_reverts_refresh_state() -> None:
    await WORKSPACES.create(WorkspaceCreate(name="Core", repos=["acme/api"]), "ramon")
    await WORKSPACES.mark_refreshing("core")
    # What an admin edit read before the refresh settled.
    stale = await WORKSPACES.get("core")
    await WORKSPACES.mark_refresh_settled("core", "success", log="done")

    with patch.object(WORKSPACES, "get", AsyncMock(return_value=stale)):
        edited = await WORKSPACES.apply_update("core", WorkspaceUpdate(prompt="edited"))

    stored = await WORKSPACES.get("core")
    assert stored is not None
    assert stored.prompt == "edited"
    assert stored.refresh_status == "success"
    assert stored.refresh_log == "done"
    assert edited == stored


# --- capture ---


def _sandbox_client(capture: AsyncMock) -> MagicMock:
    client = MagicMock()
    client.capture_snapshot = capture
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_failed_recapture_keeps_booting_from_the_previous_snapshot() -> None:
    capture = AsyncMock(side_effect=RuntimeError("capture exploded"))
    delete_snapshot = AsyncMock()
    with (
        patch.object(env_store, "_delete_snapshot", delete_snapshot),
        patch(
            "agent.sandboxes.providers.langsmith.get_async_sandbox_client",
            return_value=_sandbox_client(capture),
        ),
    ):
        await WORKSPACES.create(WorkspaceCreate(name="base", repos=["acme/base"]), "ramon")
        await WORKSPACES.mark_captured(
            "base",
            snapshot_id="snap-1",
            snapshot_name="prior",
            source_sandbox_id="sb-prior",
        )

        with pytest.raises(RuntimeError, match="capture exploded"):
            await env_store.capture_workspace_snapshot("base", "sb-123")

        record = await WORKSPACES.get("base")

    assert record is not None
    # Still ready, so runs keep booting from snap-1 instead of dropping to the
    # base image; the error rides along in status_message.
    assert record.snapshot_status == "ready"
    assert record.ready_snapshot_id == "snap-1"
    assert record.status_message == "capture exploded"
    delete_snapshot.assert_not_awaited()


# --- per-thread selection ---


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_workspace_options_omit_admin_only_settings() -> None:
    await WORKSPACES.apply_update(
        "default",
        WorkspaceUpdate(prompt="secret-ish prompt", create_params={"_internal_runtime": "v2"}),
    )
    await WORKSPACES.mark_captured(
        "default",
        snapshot_id="snap-1",
        snapshot_name="prior",
        source_sandbox_id="sb-prior",
    )
    await WORKSPACES.mark_refresh_settled("default", "success", log="+ TOKEN=hunter2\ndone")
    options = await env_store.list_workspace_options()

    # No prompt, no snapshot id — and no log: `bash -x` expands arguments, so a
    # script that put a credential on a command line has written it there.
    assert options == [
        {
            "slug": "default",
            "name": "Default",
            "repos": [],
            "slack_channel_ids": [],
            "is_default": True,
            "has_snapshot": True,
            "refresh_status": "success",
            "refresh_kind": None,
            "refresh_finished_at": options[0]["refresh_finished_at"],
            "refresh_error": None,
            "refresh_steps": [],
        }
    ]
    assert options[0]["refresh_finished_at"]

    admin_view = await env_store.list_workspace_options(include_logs=True)
    assert admin_view[0]["refresh_log_excerpt"] == "+ TOKEN=hunter2\ndone"


@pytest.mark.asyncio
@pytest.mark.usefixtures("registry_db")
async def test_publish_writes_definition_and_image_together() -> None:
    """One put carries both, so a record can never show a new definition on an old image."""
    record = await WORKSPACES.publish(
        "base",
        WorkspaceCreate(name="base", repos=["acme/base"], prompt="p1", setup_script="make setup"),
        snapshot_id="snap-1",
        snapshot_name="openswe-environment-base",
        source_sandbox_id="sb-1",
        created_by="ramon",
    )
    assert (record.prompt, record.snapshot_id, record.snapshot_status) == ("p1", "snap-1", "ready")
    assert record.created_by == "ramon"

    updated = await WORKSPACES.publish(
        "base",
        WorkspaceUpdate(prompt="p2"),
        snapshot_id="snap-2",
        snapshot_name="openswe-environment-base",
        source_sandbox_id="sb-2",
        created_by="ramon",
    )
    stored = await WORKSPACES.get("base")
    assert stored is not None
    assert (stored.prompt, stored.snapshot_id) == ("p2", "snap-2")
    assert stored.setup_script == "make setup"
    assert updated.source_sandbox_id == "sb-2"


# --- Slack channels, uniqueness, and the one-off Store import ---


# --- rows ---


@pytest.mark.usefixtures("registry_db")
async def test_an_update_releases_the_bindings_it_drops() -> None:
    await WORKSPACES.create(
        WorkspaceCreate(
            name="Core", repos=["acme/api", "acme/web"], slack_channel_ids=["C0API", "C0WEB"]
        ),
        "alice",
    )

    await WORKSPACES.apply_update(
        "core", WorkspaceUpdate(repos=["acme/web"], slack_channel_ids=["C0WEB"])
    )

    stored = await WORKSPACES.get("core")
    assert stored is not None
    assert stored.repos == ["acme/web"]
    assert stored.slack_channel_ids == ["C0WEB"]
    # What it let go of is free for another workspace to claim.
    released = await WORKSPACES.create(
        WorkspaceCreate(name="OSS", repos=["acme/api"], slack_channel_ids=["C0API"]), "alice"
    )
    assert released.repos == ["acme/api"]
    assert await WORKSPACES.owner_of_slack_channel("C0API") == "oss"


@pytest.mark.usefixtures("registry_db")
async def test_a_claim_that_races_the_pre_check_still_names_the_owner() -> None:
    """The primary keys are the guarantee; the pre-check only makes it readable.

    ``put`` is where a create lands once its pre-check has passed, so writing
    through it is the claim that arrives while another one is in flight.
    """
    await WORKSPACES.create(
        WorkspaceCreate(name="Core", repos=["acme/api"], slack_channel_ids=["C0API"]), "alice"
    )

    with pytest.raises(ValueError, match="acme/api already belongs to workspace core"):
        await WORKSPACES.put("oss", Workspace(slug="oss", name="OSS", repos=["acme/api"]))
    with pytest.raises(ValueError, match="C0API already belongs to workspace core"):
        await WORKSPACES.put(
            "oss",
            Workspace(slug="oss", name="OSS", repos=["acme/oss"], slack_channel_ids=["C0API"]),
        )

    # Neither claim left a half-written workspace behind.
    assert await WORKSPACES.get("oss") is None
