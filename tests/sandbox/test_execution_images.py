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
from openswe.sandboxes.image_build import (
    BUILDER_DELETE_AFTER_STOP_SECONDS,
    RefreshProgress,
    SnapshotBuild,
)


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
        patch.object(SnapshotBuild, "release_builder_sandbox", release),
        patch.object(SnapshotBuild, "capture_sandbox_snapshot", AsyncMock(side_effect=captured)),
    ):
        result = await EXECUTION_IMAGES.refresh(job)
    saved = await EXECUTION_DEFAULTS.load()
    published = change in {"none", "disabled", "prompt"}
    assert result["status"] == ("success" if published else "failed")
    assert saved.snapshot_id == (
        "snap-new" if published else "other-snapshot" if change == "snapshot" else "snap-old"
    )
    state = await EXECUTION_IMAGES.state()
    assert bool(state.captured_at(saved.snapshot_id)) is published
    assert state.captured_at("unrelated-snapshot") is None
    assert create.await_args is not None
    assert create.await_args.kwargs == {
        "snapshot_id": "snap-base",
        "create_params": {
            **params,
            "delete_after_stop_seconds": BUILDER_DELETE_AFTER_STOP_SECONDS,
        },
        "vcpus": 4,
        "mem_bytes": 8_000_000_000,
    }
    assert proxy.await_args is not None
    assert (
        proxy.await_args.kwargs["base_proxy_config"]["rules"][0]["aws"]["subject_scope"]
        == f"tag:{tag}"
    )
    command = backend.aexecute.call_args_list[0].args[0]
    assert "ROBIN_REPOS=acme/preload" in command
    assert "OPENSWE_REPOS=acme/preload" in command
    assert "OPENSWE_WORKSPACE_REPOS=acme/preload" in command
    release.assert_awaited_once_with("sb-builder")


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
        patch.object(SnapshotBuild, "create_builder_sandbox", create),
        patch.object(SnapshotBuild, "release_builder_sandbox", release),
        patch.object(SnapshotBuild, "capture_sandbox_snapshot", AsyncMock(return_value="new")),
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
        patch.object(SnapshotBuild, "create_builder_sandbox", create),
        patch.object(SnapshotBuild, "release_builder_sandbox", AsyncMock()),
        patch.object(SnapshotBuild, "capture_sandbox_snapshot", AsyncMock(return_value="new")),
    ):
        assert (await EXECUTION_IMAGES.refresh(job))["status"] == "success"
    assert create.await_args is not None and create.await_args.args[1] == "old"
    assert _scripts_run(backend) == ["git pull"]
    await job.finish()
    assert await EXECUTION_IMAGES.maybe_start_update() is None
    state = await EXECUTION_IMAGES.state()
    assert state.captured_at("new") is not None
    assert state.captured_at("old") is None


@pytest.mark.parametrize("failure", ["boot", "setup", "update", "cancel"])
async def test_failed_builder_never_captures_and_releases_only_its_own_sandbox(
    failure: str,
) -> None:
    definition = ExecutionDefaults(setup_script="make setup", update_script="git pull")
    backend = _backend(_Result("setup", 1 if failure == "setup" else 0), _Result("update", 1))
    if failure == "cancel":
        backend.aexecute.side_effect = asyncio.CancelledError()
    create = AsyncMock(
        return_value=backend,
        side_effect=RuntimeError("no capacity") if failure == "boot" else None,
    )
    release, capture = AsyncMock(), AsyncMock()
    progress = AsyncMock(spec=RefreshProgress)
    build = SnapshotBuild(definition, None, "full", progress, capture)
    with (
        patch.object(SnapshotBuild, "create_builder_sandbox", create),
        patch.object(SnapshotBuild, "release_builder_sandbox", release),
    ):
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await build.run()
        else:
            result = await build.run()
            assert result["status"] == "failed"
            if failure == "update":
                assert result["script"] == "update"
                assert _scripts_run(backend) == ["make setup", "git pull"]
    capture.assert_not_awaited()
    assert progress.settle.await_args.args == ("failed",)
    if failure == "boot":
        release.assert_not_awaited()
    else:
        release.assert_awaited_once_with("sb-builder")
