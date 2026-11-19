import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import JsonValue
from sqlalchemy import text

from openswe.dashboard.execution_defaults import EXECUTION_DEFAULTS, ExecutionDefaults
from openswe.database import transaction
from openswe.execution_images import EXECUTION_IMAGES
from openswe.jobs import queue
from openswe.webhooks import event_log
from openswe.workspaces import refresh
from openswe.workspaces.store import WORKSPACES, WorkspaceCreate


class _Result:
    def __init__(self, output: str, exit_code: int) -> None:
        self.output = output
        self.exit_code = exit_code


def _backend(*results: _Result) -> MagicMock:
    backend = MagicMock()
    backend.id = "sb-builder"
    backend.aexecute = AsyncMock(side_effect=list(results))
    return backend


def _scripts_run(backend: MagicMock) -> list[str]:
    """The script bodies the builder actually executed, in order."""
    bodies = []
    for call in backend.aexecute.call_args_list:
        encoded = str(call.args[0]).split("printf %s ")[1].split(" |")[0].strip("'")
        bodies.append(base64.b64decode(encoded).decode())
    return bodies


# --- refresh ---


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_the_update_script_runs_after_setup_and_gates_the_capture() -> None:
    """A broken update script must be caught here, not by the next hourly update."""
    backend = _backend(_Result("provisioned", 0), _Result("fatal: not a git repository", 1))
    capture = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_workspace_snapshot", capture),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
                update_script="git pull",
            ),
            "ramon",
        )
        result = await refresh.refresh_workspace("base")
        record = await WORKSPACES.get("base")

    assert result["status"] == "failed"
    assert result["script"] == "update"
    capture.assert_not_awaited()
    assert _scripts_run(backend) == ["make setup", "git pull"]
    assert record is not None
    assert record.refresh_error == "update script exited 1"
    # Both sections ride along, so the model can see what ran before the break.
    assert record.refresh_log is not None
    assert "--- setup script ---" in record.refresh_log
    assert "fatal: not a git repository" in record.refresh_log


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_a_failing_script_is_never_captured() -> None:
    backend = _backend(_Result("gcc: fatal error", 2))
    capture = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_workspace_snapshot", capture),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
            ),
            "ramon",
        )
        # A snapshot from an earlier refresh; runs must keep booting from it.
        await WORKSPACES.mark_captured(
            "base",
            snapshot_id="snap-1",
            snapshot_name="openswe-environment-base",
            source_sandbox_id="sb-prior",
        )
        result = await refresh.refresh_workspace("base")
        record = await WORKSPACES.get("base")

    assert result["status"] == "failed"
    capture.assert_not_awaited()
    assert record is not None
    assert record.refresh_status == "failed"
    assert record.refresh_error == "setup script exited 2"
    assert record.ready_snapshot_id == "snap-1"


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_a_sandbox_that_never_boots_still_records_the_failure() -> None:
    release = AsyncMock()
    with (
        patch.object(
            refresh,
            "_create_builder_sandbox",
            AsyncMock(side_effect=RuntimeError("no capacity")),
        ),
        patch.object(refresh, "release_builder_sandbox", release),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
            ),
            "ramon",
        )
        result = await refresh.refresh_workspace("base")
        record = await WORKSPACES.get("base")

    assert result["status"] == "failed"
    assert result["error"] == "no capacity"
    release.assert_not_awaited()
    assert record is not None
    assert record.refresh_error == "no capacity"


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capture_error",
    [None, TimeoutError(), RuntimeError("capture failed with opaque-build-credential")],
)
async def test_refresh_history_keeps_outcomes_without_raw_diagnostics(
    capture_error: Exception | None,
) -> None:
    """Historical events must not copy arbitrary credentials from logs or exception messages."""
    backend = _backend(_Result("Updating repo with opaque-build-credential", 0))
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_workspace_snapshot", AsyncMock(side_effect=capture_error)),
        patch.object(event_log, "_ROTATED_AT", None),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
            ),
            "ramon",
        )
        await refresh.refresh_workspace("base")
        record = await WORKSPACES.get("base")

    assert record is not None
    status = "failed" if capture_error is not None else "success"
    expected_error = (str(capture_error) or type(capture_error).__name__) if capture_error else None
    assert record.refresh_error == expected_error
    assert record.refresh_log is not None
    assert "opaque-build-credential" in record.refresh_log
    async with transaction() as conn:
        rows = await conn.execute(
            text(
                "SELECT event_type, payload, workspace_id FROM event_log WHERE source = 'workspace'"
            )
        )
        [(event_type, payload, workspace_id)] = rows.tuples().all()
    assert event_type == f"refresh.{status}"
    assert workspace_id == await WORKSPACES.id_for_slug("base")
    assert "error" not in payload
    assert "log_tail" not in payload
    assert "opaque-build-credential" not in str(payload)
    assert [(step["label"], step["status"]) for step in payload["steps"]] == [
        ("boot", "success"),
        ("setup", "success"),
        ("capture", status),
    ]


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_a_refresh_in_flight_blocks_a_second_one_but_not_its_own_retry() -> None:
    """A job retried after a redeploy cut it short must not wait hours behind itself."""
    create = AsyncMock(return_value=_backend(_Result("provisioned", 0)))
    release = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", create),
        patch.object(refresh, "release_builder_sandbox", release),
        patch.object(refresh, "capture_workspace_snapshot", AsyncMock()),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
            ),
            "ramon",
        )
        original = await WORKSPACES.mark_refreshing("base", run_id="run-a")
        assert original is not None
        await original.publish_builder("sb-lost")
        other = await refresh.refresh_workspace("base", run_id="run-b")
        create.assert_not_awaited()
        retried = await refresh.refresh_workspace("base", run_id="run-a")
        # A stalled original that outlived its lease can no longer settle over the retry.
        await original.settle("failed", error="builder stopped")
        record = await WORKSPACES.get("base")

    assert other["status"] == "already_refreshing"
    assert retried["status"] == "success"
    assert record is not None
    assert record.refresh_status == "success"
    assert record.refresh_error is None
    # The cut-short attempt's builder is stopped, not leaked.
    assert [call.args[0] for call in release.await_args_list] == ["sb-lost", "sb-builder"]


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_a_queued_job_never_takes_over_a_cron_refresh_that_claimed_first() -> None:
    create = AsyncMock()
    with patch.object(refresh, "_create_builder_sandbox", create):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
            ),
            "ramon",
        )
        # Queueing a job writes its id before the job runs; the nightly cron claims meanwhile.
        await WORKSPACES.set_refresh_run_id("base", "job-y")
        assert await WORKSPACES.mark_refreshing("base") is not None
        result = await refresh.refresh_workspace("base", run_id="job-y")

    assert result["status"] == "already_refreshing"
    create.assert_not_awaited()


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_a_cancelled_refresh_is_settled_so_its_retry_can_run() -> None:
    backend = _backend()
    backend.aexecute = AsyncMock(side_effect=asyncio.CancelledError)
    release = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "release_builder_sandbox", release),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
            ),
            "ramon",
        )
        with pytest.raises(asyncio.CancelledError):
            await refresh.refresh_workspace("base", run_id="run-a")
        record = await WORKSPACES.get("base")

    release.assert_awaited_once_with("sb-builder")
    assert record is not None
    assert record.refresh_status == "failed"
    assert record.refresh_error == "interrupted"
    assert not record.refresh_in_flight()


# --- update kind + lazy trigger ---


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_an_update_boots_from_the_current_snapshot_and_runs_only_the_update_script() -> None:
    backend = _backend(_Result("Already up to date.", 0))
    create_builder = AsyncMock(return_value=backend)
    capture = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", create_builder),
        patch.object(refresh, "release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_workspace_snapshot", capture),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
                update_script="git pull",
            ),
            "ramon",
        )
        await WORKSPACES.mark_captured(
            "base",
            snapshot_id="snap-1",
            snapshot_name="openswe-environment-base",
            source_sandbox_id="sb-prior",
        )
        result = await refresh.refresh_workspace("base", "update")
        record = await WORKSPACES.get("base")

    assert result["status"] == "success"
    assert result["kind"] == "update"
    # From the live snapshot, not the base image.
    assert create_builder.await_args is not None
    assert create_builder.await_args.args[1] == "snap-1"
    assert _scripts_run(backend) == ["git pull"]
    capture.assert_awaited_once()
    assert record is not None
    assert record.refresh_kind == "update"


# --- cron ---


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_the_step_that_broke_is_the_one_left_failed() -> None:
    backend = _backend(_Result("gcc: fatal error", 2))
    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_workspace_snapshot", AsyncMock()),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
            ),
            "ramon",
        )
        await refresh.refresh_workspace("base")
        record = await WORKSPACES.get("base")

    assert record is not None
    assert [(step.label, step.status, step.exit_code) for step in record.refresh_steps] == [
        ("boot", "success", None),
        ("setup", "failed", 2),
    ]


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_the_builder_is_published_while_it_lives_and_cleared_after() -> None:
    """A poll reads the running trace off the builder, so its id must be current."""
    backend = _backend(_Result("provisioned", 0))
    published: list[str | None] = []

    async def _capture(slug: str, sandbox_id: str, **_: object) -> None:
        record = await WORKSPACES.get(slug)
        published.append(record.refresh_sandbox_id if record else None)

    with (
        patch.object(refresh, "_create_builder_sandbox", AsyncMock(return_value=backend)),
        patch.object(refresh, "release_builder_sandbox", AsyncMock()),
        patch.object(refresh, "capture_workspace_snapshot", _capture),
    ):
        await WORKSPACES.create(
            WorkspaceCreate(
                inherit_default_sandbox=False,
                name="base",
                repos=["acme/base"],
                setup_script="make setup",
            ),
            "ramon",
        )
        await refresh.refresh_workspace("base")
        record = await WORKSPACES.get("base")

    assert published == ["sb-builder"]
    assert record is not None
    # Released with the refresh, so it stops being offered as a readable source.
    assert record.refresh_sandbox_id is None


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "none",
        "script_failure",
        "capture_failure",
        "definition",
        "snapshot",
        "claim",
        "expired",
        "disabled",
        "prompt",
    ],
)
async def test_global_publication_requires_the_original_definition_and_live_queue_claim(
    change: str,
) -> None:
    from openswe.sandboxes.providers import langsmith

    tag = "00000000-0000-0000-0000-000000000001"
    params: dict[str, JsonValue] = {
        "run_config": {"env_vars": {"PATH": "/usr/bin:/bin"}},
        "tag_value_ids": [tag],
        "proxy_config": {
            "rules": [
                {
                    "name": "aws",
                    "type": "aws",
                    "enabled": True,
                    "aws": {
                        "role_arn": "arn:aws:iam::123456789012:role/ReadOnly",
                        "subject_scope": f"tag:{tag}",
                    },
                }
            ]
        },
        "access_delegation": {
            "mode": "EXPLICIT",
            "permissions": ["sandboxes:create", "projects:read"],
        },
    }
    definition = ExecutionDefaults(
        enabled=True,
        repos=["acme/preload"],
        setup_script="make setup",
        update_script="git pull",
        snapshot_id="snap-old",
        snapshot_name="shared-legacy-name",
        base_snapshot_id="snap-base",
        vcpus=4,
        mem_bytes=8_000_000_000,
        create_params=params,
    )
    await EXECUTION_DEFAULTS.records.put("", definition.model_dump(mode="json"))
    await WORKSPACES.create(WorkspaceCreate(name="legacy", repos=["acme/preload"]), "admin")
    await WORKSPACES.mark_captured(
        "legacy", snapshot_id="legacy-old", snapshot_name="legacy-name", source_sandbox_id="sb-old"
    )
    run_id = await EXECUTION_IMAGES.start()
    [job] = await queue.claim_due(1, kinds=[EXECUTION_IMAGES.JOB])
    assert str(job.id) == run_id
    backend = _backend(
        _Result("setup", 2 if change == "script_failure" else 0), _Result("updated", 0)
    )
    create = AsyncMock(return_value=backend)
    proxy = AsyncMock()
    release = AsyncMock()

    async def captured(*_: object, **__: object) -> str:
        if change == "capture_failure":
            raise RuntimeError("snapshot capture unavailable")
        if change == "definition":
            await EXECUTION_DEFAULTS.records.patch("", {"setup_script": "different setup"})
        elif change == "snapshot":
            await EXECUTION_DEFAULTS.records.patch("", {"snapshot_id": "other-snapshot"})
        elif change == "disabled":
            await EXECUTION_DEFAULTS.records.patch("", {"enabled": False})
        elif change == "prompt":
            await EXECUTION_DEFAULTS.records.patch("", {"prompt": "changed while building"})
        elif change == "claim":
            await queue.schedule(EXECUTION_IMAGES.JOB, job.payload, key=EXECUTION_IMAGES.KEY)
        elif change == "expired":
            async with transaction() as conn:
                await conn.execute(
                    text(
                        "UPDATE job SET locked_until = clock_timestamp() - interval '1 second' WHERE id = :id"
                    ),
                    {"id": job.id},
                )
        return "snap-new"

    with (
        patch(
            "openswe.github.sandbox_access.installation_token",
            AsyncMock(return_value=SimpleNamespace(token="ephemeral")),
        ),
        patch.object(langsmith, "create_langsmith_sandbox", create),
        patch.object(langsmith, "configure_sandbox_proxy", proxy),
        patch.object(refresh, "release_builder_sandbox", release),
        patch("openswe.execution_images.capture_sandbox_snapshot", AsyncMock(side_effect=captured)),
    ):
        result = await EXECUTION_IMAGES.refresh(job)
    saved = await EXECUTION_DEFAULTS.load()
    published = change in {"none", "disabled", "prompt"}
    assert result["status"] == ("success" if published else "failed")
    assert saved.snapshot_id == (
        "snap-new" if published else "other-snapshot" if change == "snapshot" else "snap-old"
    )
    legacy = await WORKSPACES.get("legacy")
    assert legacy is not None and legacy.snapshot_id == "legacy-old"
    state = await EXECUTION_IMAGES.state()
    assert bool(state.captured_at(saved.snapshot_id)) is published
    assert state.captured_at("unrelated-snapshot") is None
    assert create.await_args is not None
    assert create.await_args.kwargs == {
        "snapshot_id": "snap-base",
        "create_params": {
            **params,
            "delete_after_stop_seconds": refresh.BUILDER_DELETE_AFTER_STOP_SECONDS,
        },
        "vcpus": 4,
        "mem_bytes": 8_000_000_000,
    }
    assert proxy.await_args is not None
    assert (
        proxy.await_args.kwargs["base_proxy_config"]["rules"][0]["aws"]["subject_scope"]
        == f"tag:{tag}"
    )
    assert "OPENSWE_WORKSPACE_REPOS=acme/preload" in backend.aexecute.call_args_list[0].args[0]
    release.assert_awaited_once_with("sb-builder")


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_job_targets_stay_legacy_or_global_across_toggle_changes() -> None:
    await WORKSPACES.create(
        WorkspaceCreate(
            name="legacy",
            setup_script="make legacy",
            repos=["acme/legacy"],
            inherit_default_sandbox=False,
        ),
        "admin",
    )
    await EXECUTION_DEFAULTS.records.put("", {"enabled": False, "setup_script": "make global"})
    assert await EXECUTION_IMAGES.ensure_refresh_cron() is None
    assert await EXECUTION_IMAGES.maybe_start_update() is None
    with pytest.raises(ValueError, match="disabled"):
        await EXECUTION_IMAGES.start()
    await EXECUTION_DEFAULTS.records.patch("", {"enabled": True})
    assert await EXECUTION_IMAGES.ensure_refresh_cron() is not None
    run_id = await EXECUTION_IMAGES.start()
    [job] = await queue.claim_due(1, kinds=[EXECUTION_IMAGES.JOB])
    assert str(job.id) == run_id
    create = AsyncMock(return_value=_backend(_Result("legacy", 0)))
    with (
        patch.object(refresh, "_create_builder_sandbox", create),
        patch.object(refresh, "capture_workspace_snapshot", AsyncMock()),
        patch.object(refresh, "release_builder_sandbox", AsyncMock()),
    ):
        assert (await refresh.run_workspace_refresh_tick("legacy"))[
            "status"
        ] == "global_execution_enabled"
        create.assert_not_awaited()
        await EXECUTION_DEFAULTS.records.patch("", {"enabled": False})
        assert (await EXECUTION_IMAGES.refresh(job))["status"] == "global_execution_disabled"
        create.assert_not_awaited()
        assert (await refresh.run_workspace_refresh_tick("legacy"))["status"] == "success"
    definition = await EXECUTION_DEFAULTS.load()
    assert definition.snapshot_id is None
    await EXECUTION_DEFAULTS.records.patch("", {"enabled": True, "setup_script": ""})
    cron = await queue.get(EXECUTION_IMAGES.CRON_KEY)
    assert cron is not None
    assert (await EXECUTION_IMAGES.refresh(cron))["status"] == "no_setup_script"


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_global_publication_never_reclaims_the_thread_sandbox() -> None:
    await EXECUTION_DEFAULTS.records.put("", {"enabled": True, "snapshot_id": "old"})
    await EXECUTION_DEFAULTS.records.put("image", {"refresh_sandbox_id": "sb-thread"})
    run_id = await EXECUTION_IMAGES.publish("sb-thread")
    [job] = await queue.claim_due(1, kinds=[EXECUTION_IMAGES.JOB])
    assert str(job.id) == run_id
    release = AsyncMock()
    create = AsyncMock()
    with (
        patch.object(refresh, "_create_builder_sandbox", create),
        patch.object(refresh, "release_builder_sandbox", release),
        patch("openswe.execution_images.capture_sandbox_snapshot", AsyncMock(return_value="new")),
    ):
        assert (await EXECUTION_IMAGES.refresh(job))["status"] == "success"
    assert (await EXECUTION_DEFAULTS.load()).snapshot_id == "new"
    create.assert_not_awaited()
    release.assert_not_awaited()


@pytest.mark.usefixtures("registry_db")
@pytest.mark.asyncio
async def test_global_lazy_update_reuses_the_current_image_and_publishes_freshness() -> None:
    await EXECUTION_DEFAULTS.records.put(
        "",
        {
            "enabled": True,
            "snapshot_id": "old",
            "base_snapshot_id": "base",
            "update_script": "git pull",
        },
    )
    await EXECUTION_DEFAULTS.records.put(
        "image", {"snapshot_id": "old", "last_captured_at": "2000-01-01T00:00:00Z"}
    )
    run_id = await EXECUTION_IMAGES.maybe_start_update()
    assert run_id is not None
    assert await EXECUTION_IMAGES.maybe_start_update() == run_id
    [job] = await queue.claim_due(1, kinds=[EXECUTION_IMAGES.JOB])
    backend = _backend(_Result("updated", 0))
    create = AsyncMock(return_value=backend)
    with (
        patch.object(refresh, "_create_builder_sandbox", create),
        patch.object(refresh, "release_builder_sandbox", AsyncMock()),
        patch("openswe.execution_images.capture_sandbox_snapshot", AsyncMock(return_value="new")),
    ):
        assert (await EXECUTION_IMAGES.refresh(job))["status"] == "success"
    assert create.await_args is not None and create.await_args.args[1] == "old"
    assert _scripts_run(backend) == ["git pull"]
    await job.finish()
    assert await EXECUTION_IMAGES.maybe_start_update() is None
    state = await EXECUTION_IMAGES.state()
    assert state.captured_at("new") is not None
    assert state.captured_at("old") is None
