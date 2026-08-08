from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError

from agent.dashboard import base_snapshot, repo_clone_stats, routes
from agent.utils.repo_clone import build_mirror_sweep_script, parse_mirror_sweep_output


def _iso(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def _stat(full_name: str, count: int, days_ago: float) -> dict[str, object]:
    return {
        "full_name": full_name,
        "clone_count": count,
        "first_cloned_at": _iso(days_ago + 1),
        "last_cloned_at": _iso(days_ago),
    }


@pytest.mark.asyncio
async def test_record_repo_clone_increments_existing_count() -> None:
    store = MagicMock()
    store.get_item = AsyncMock(
        return_value={"value": {"full_name": "acme/repo", "clone_count": 4, "first_cloned_at": "x"}}
    )
    store.put_item = AsyncMock()
    with patch.object(repo_clone_stats, "_client", return_value=SimpleNamespace(store=store)):
        await repo_clone_stats.record_repo_clone("acme", "repo")

    _namespace, key, value = store.put_item.await_args.args
    assert key == "acme/repo"
    assert value["clone_count"] == 5
    assert value["first_cloned_at"] == "x"


@pytest.mark.asyncio
async def test_record_repo_clone_swallows_store_failures() -> None:
    store = MagicMock()
    store.get_item = AsyncMock(side_effect=RuntimeError("store down"))
    with patch.object(repo_clone_stats, "_client", return_value=SimpleNamespace(store=store)):
        await repo_clone_stats.record_repo_clone("acme", "repo")


@pytest.mark.asyncio
async def test_repos_to_preclone_orders_by_count_and_drops_stale() -> None:
    stats = [
        _stat("acme/cold", 99, days_ago=90),
        _stat("acme/busy", 30, days_ago=1),
        _stat("acme/quiet", 2, days_ago=2),
    ]
    with patch.object(repo_clone_stats, "list_repo_clone_stats", AsyncMock(return_value=stats)):
        repos = await repo_clone_stats.repos_to_preclone(limit=5, max_age_days=30)
    assert repos == ["acme/busy", "acme/quiet"]


@pytest.mark.asyncio
async def test_repos_to_preclone_respects_limit() -> None:
    stats = [_stat(f"acme/r{i}", count=i, days_ago=1) for i in range(10)]
    with patch.object(repo_clone_stats, "list_repo_clone_stats", AsyncMock(return_value=stats)):
        repos = await repo_clone_stats.repos_to_preclone(limit=3, max_age_days=30)
    assert repos == ["acme/r9", "acme/r8", "acme/r7"]


def test_settings_reject_bad_schedule() -> None:
    with pytest.raises(ValidationError):
        base_snapshot.BaseSnapshotSettings(schedule="not a cron")


def test_settings_reject_out_of_range_limits() -> None:
    with pytest.raises(ValidationError):
        base_snapshot.BaseSnapshotSettings(preclone_limit=0)
    with pytest.raises(ValidationError):
        base_snapshot.BaseSnapshotSettings(max_age_days=9999)
    with pytest.raises(ValidationError):
        base_snapshot.BaseSnapshotSettings(keep_snapshots=0)


def test_corrupt_stored_settings_fall_back_to_defaults() -> None:
    settings = base_snapshot._coerce_settings({"schedule": "garbage", "preclone_limit": -4})
    assert settings.enabled is False
    assert settings.schedule == base_snapshot.DEFAULT_SCHEDULE
    assert settings.preclone_limit == base_snapshot.DEFAULT_PRECLONE_LIMIT


@pytest.mark.asyncio
async def test_resolve_base_snapshot_id_returns_none_when_disabled() -> None:
    record = {"status": "ready", "snapshot_id": "snap-1", "settings": {"enabled": False}}
    with patch.object(base_snapshot, "_read_record", AsyncMock(return_value=record)):
        assert await base_snapshot.resolve_base_snapshot_id() is None


@pytest.mark.asyncio
async def test_resolve_base_snapshot_id_returns_ready_snapshot() -> None:
    record = {"status": "ready", "snapshot_id": "snap-1", "settings": {"enabled": True}}
    with patch.object(base_snapshot, "_read_record", AsyncMock(return_value=record)):
        assert await base_snapshot.resolve_base_snapshot_id() == "snap-1"


@pytest.mark.asyncio
async def test_resolve_base_snapshot_id_ignores_failed_record() -> None:
    record = {"status": "failed", "snapshot_id": "snap-1", "settings": {"enabled": True}}
    with patch.object(base_snapshot, "_read_record", AsyncMock(return_value=record)):
        assert await base_snapshot.resolve_base_snapshot_id() is None


@pytest.mark.asyncio
async def test_enabling_settings_creates_cron_and_persists_id() -> None:
    crons = MagicMock()
    crons.create = AsyncMock(return_value={"cron_id": "cron-1"})
    put = AsyncMock()
    with (
        patch.object(base_snapshot, "_read_record", AsyncMock(return_value={})),
        patch.object(base_snapshot, "_put_record", put),
        patch.object(base_snapshot, "_client", return_value=SimpleNamespace(crons=crons)),
    ):
        record = await base_snapshot.update_base_snapshot_settings(
            base_snapshot.BaseSnapshotSettings(enabled=True, schedule="30 4 * * *")
        )

    assert record["cron_id"] == "cron-1"
    assert record["cron_schedule"] == "30 4 * * *"
    assert crons.create.await_args.kwargs["schedule"] == "30 4 * * *"
    put.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabling_settings_deletes_cron() -> None:
    crons = MagicMock()
    crons.create = AsyncMock()
    crons.delete = AsyncMock()
    existing = {"cron_id": "cron-1", "cron_schedule": "0 9 * * *"}
    with (
        patch.object(base_snapshot, "_read_record", AsyncMock(return_value=existing)),
        patch.object(base_snapshot, "_put_record", AsyncMock()),
        patch.object(base_snapshot, "_client", return_value=SimpleNamespace(crons=crons)),
    ):
        record = await base_snapshot.update_base_snapshot_settings(
            base_snapshot.BaseSnapshotSettings(enabled=False)
        )

    crons.delete.assert_awaited_once_with("cron-1")
    crons.create.assert_not_awaited()
    assert record["cron_id"] is None


@pytest.mark.asyncio
async def test_unchanged_schedule_reuses_existing_cron() -> None:
    crons = MagicMock()
    crons.create = AsyncMock()
    crons.delete = AsyncMock()
    existing = {
        "cron_id": "cron-1",
        "cron_schedule": "0 9 * * *",
        "settings": {"enabled": True, "schedule": "0 9 * * *"},
    }
    with (
        patch.object(base_snapshot, "_read_record", AsyncMock(return_value=existing)),
        patch.object(base_snapshot, "_put_record", AsyncMock()),
        patch.object(base_snapshot, "_client", return_value=SimpleNamespace(crons=crons)),
    ):
        record = await base_snapshot.update_base_snapshot_settings(
            base_snapshot.BaseSnapshotSettings(enabled=True, schedule="0 9 * * *")
        )

    crons.create.assert_not_awaited()
    crons.delete.assert_not_awaited()
    assert record["cron_id"] == "cron-1"


@pytest.mark.asyncio
async def test_changed_schedule_replaces_cron() -> None:
    crons = MagicMock()
    crons.create = AsyncMock(return_value={"cron_id": "cron-2"})
    crons.delete = AsyncMock()
    existing = {"cron_id": "cron-1", "cron_schedule": "0 9 * * *"}
    with (
        patch.object(base_snapshot, "_read_record", AsyncMock(return_value=existing)),
        patch.object(base_snapshot, "_put_record", AsyncMock()),
        patch.object(base_snapshot, "_client", return_value=SimpleNamespace(crons=crons)),
    ):
        record = await base_snapshot.update_base_snapshot_settings(
            base_snapshot.BaseSnapshotSettings(enabled=True, schedule="15 3 * * *")
        )

    crons.delete.assert_awaited_once_with("cron-1")
    assert record["cron_id"] == "cron-2"


@pytest.mark.asyncio
async def test_rebuild_keeps_previous_snapshot_id_on_failure() -> None:
    previous = {"status": "ready", "snapshot_id": "snap-old", "settings": {"enabled": True}}
    put = AsyncMock()
    with (
        patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "seed-1"}),
        patch.object(base_snapshot, "_read_record", AsyncMock(return_value=previous)),
        patch.object(
            base_snapshot, "mark_base_snapshot_building", AsyncMock(return_value=previous)
        ),
        patch.object(base_snapshot, "repos_to_preclone", AsyncMock(return_value=["acme/repo"])),
        patch.object(base_snapshot, "_put_record", put),
        patch(
            "agent.utils.github_app.get_github_app_installation_token",
            AsyncMock(return_value=None),
        ),
    ):
        record = await base_snapshot.rebuild_base_snapshot()

    assert record["status"] == "failed"
    assert record["snapshot_id"] == "snap-old"
    put.assert_awaited_once()


@pytest.mark.asyncio
async def test_rebuild_uses_stored_limits() -> None:
    previous = {"settings": {"enabled": True, "preclone_limit": 4, "max_age_days": 7}}
    preclone = AsyncMock(return_value=[])
    with (
        patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "seed-1"}),
        patch.object(base_snapshot, "_read_record", AsyncMock(return_value=previous)),
        patch.object(base_snapshot, "repos_to_preclone", preclone),
        patch.object(base_snapshot, "_put_record", AsyncMock()),
    ):
        await base_snapshot.rebuild_base_snapshot()

    preclone_call = preclone.await_args
    assert preclone_call is not None
    assert preclone_call.kwargs == {"limit": 4, "max_age_days": 7}


def _builder_client() -> tuple[Any, Any]:
    sandbox = SimpleNamespace(name="openswe-base-builder-x")
    sandbox.run = AsyncMock(
        return_value=SimpleNamespace(stdout="ok acme/one\nfail acme/two\n", stderr="", exit_code=0)
    )
    client = MagicMock()
    client.create_sandbox = AsyncMock(return_value=sandbox)
    client.capture_snapshot = AsyncMock(return_value=SimpleNamespace(id="snap-new"))
    client.stop_sandbox = AsyncMock()
    return sandbox, client


@pytest.mark.asyncio
async def test_rebuild_captures_snapshot_and_stops_builder() -> None:
    _sandbox, client = _builder_client()
    prune = AsyncMock()

    with (
        patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "seed-1"}),
        patch.object(
            base_snapshot,
            "_read_record",
            AsyncMock(return_value={"settings": {"enabled": True, "keep_snapshots": 2}}),
        ),
        patch.object(
            base_snapshot, "repos_to_preclone", AsyncMock(return_value=["acme/one", "acme/two"])
        ),
        patch.object(base_snapshot, "_put_record", AsyncMock()),
        patch.object(base_snapshot, "_prune_old_snapshots", prune),
        patch(
            "agent.utils.github_app.get_github_app_installation_token",
            AsyncMock(return_value="ghs_token"),
        ),
        patch("agent.integrations.langsmith._configure_github_proxy", AsyncMock()),
        patch("agent.integrations.langsmith.get_async_sandbox_client", return_value=client),
    ):
        record = await base_snapshot.rebuild_base_snapshot()

    assert record["status"] == "ready"
    assert record["snapshot_id"] == "snap-new"
    assert record["repos"] == ["acme/one"]
    assert record["failed_repos"] == ["acme/two"]
    client.stop_sandbox.assert_awaited_once_with("openswe-base-builder-x")
    prune_call = prune.await_args
    assert prune_call is not None
    assert prune_call.args[2] == 2


@pytest.mark.asyncio
async def test_rebuild_stops_builder_when_capture_fails() -> None:
    _sandbox, client = _builder_client()
    client.capture_snapshot = AsyncMock(side_effect=RuntimeError("capture exploded"))

    with (
        patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "seed-1"}),
        patch.object(
            base_snapshot, "_read_record", AsyncMock(return_value={"settings": {"enabled": True}})
        ),
        patch.object(base_snapshot, "repos_to_preclone", AsyncMock(return_value=["acme/one"])),
        patch.object(base_snapshot, "_put_record", AsyncMock()),
        patch(
            "agent.utils.github_app.get_github_app_installation_token",
            AsyncMock(return_value="ghs_token"),
        ),
        patch("agent.integrations.langsmith._configure_github_proxy", AsyncMock()),
        patch("agent.integrations.langsmith.get_async_sandbox_client", return_value=client),
    ):
        record = await base_snapshot.rebuild_base_snapshot()

    assert record["status"] == "failed"
    assert "capture exploded" in record["status_message"]
    client.stop_sandbox.assert_awaited_once_with("openswe-base-builder-x")


@pytest.mark.asyncio
async def test_rebuild_endpoint_rejects_when_disabled() -> None:
    with patch.object(
        routes,
        "get_base_snapshot_record",
        AsyncMock(return_value={"settings": {"enabled": False}}),
    ):
        with pytest.raises(HTTPException) as exc:
            await routes.api_rebuild_base_snapshot(BackgroundTasks(), _admin={"sub": "octo"})
    assert exc.value.status_code == 400


def test_seed_snapshot_id_is_optional() -> None:
    # The platform picks a default when none is configured; a missing seed must
    # not block a rebuild.
    with patch.dict("os.environ", {}, clear=True):
        assert base_snapshot._seed_snapshot_id() is None
    with patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "  "}):
        assert base_snapshot._seed_snapshot_id() is None
    with patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "seed-9"}):
        assert base_snapshot._seed_snapshot_id() == "seed-9"


@pytest.mark.asyncio
async def test_rebuild_runs_without_a_configured_seed() -> None:
    _sandbox, client = _builder_client()
    with (
        patch.dict("os.environ", {}, clear=True),
        patch.object(
            base_snapshot, "_read_record", AsyncMock(return_value={"settings": {"enabled": True}})
        ),
        patch.object(base_snapshot, "mark_base_snapshot_building", AsyncMock(return_value={})),
        patch.object(base_snapshot, "repos_to_preclone", AsyncMock(return_value=["acme/one"])),
        patch.object(base_snapshot, "_put_record", AsyncMock()),
        patch.object(base_snapshot, "_prune_old_snapshots", AsyncMock()),
        patch.object(base_snapshot, "_set_progress", AsyncMock()),
        patch(
            "agent.utils.github_app.get_github_app_installation_token",
            AsyncMock(return_value="ghs_token"),
        ),
        patch("agent.integrations.langsmith._configure_github_proxy", AsyncMock()),
        patch("agent.integrations.langsmith.get_async_sandbox_client", return_value=client),
    ):
        record = await base_snapshot.rebuild_base_snapshot()

    assert record["status"] == "ready"
    assert client.create_sandbox.await_args.kwargs["snapshot_id"] is None


def test_build_is_stale_only_after_the_timeout() -> None:
    assert base_snapshot.is_base_snapshot_build_stale({"status": "ready"}) is False
    assert (
        base_snapshot.is_base_snapshot_build_stale(
            {"status": "building", "build_started_at": _iso(0)}
        )
        is False
    )

    old = (
        datetime.now(UTC) - timedelta(seconds=base_snapshot.STALE_BUILD_SECONDS + 60)
    ).isoformat()
    assert (
        base_snapshot.is_base_snapshot_build_stale({"status": "building", "build_started_at": old})
        is True
    )
    # A building record with no start time can't be aged, so treat it as dead.
    assert base_snapshot.is_base_snapshot_build_stale({"status": "building"}) is True


@pytest.mark.asyncio
async def test_mark_building_sets_progress_and_start_time() -> None:
    with (
        patch.object(base_snapshot, "_read_record", AsyncMock(return_value={"status": "ready"})),
        patch.object(base_snapshot, "_put_record", AsyncMock()),
    ):
        record = await base_snapshot.mark_base_snapshot_building()

    assert record["status"] == "building"
    assert record["build_started_at"]
    assert record["progress"] == {"phase": "starting", "completed": 0, "total": 0}


@pytest.mark.asyncio
async def test_terminal_write_clears_progress_so_the_bar_cannot_stick() -> None:
    _sandbox, client = _builder_client()
    with (
        patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "seed-1"}),
        patch.object(
            base_snapshot, "_read_record", AsyncMock(return_value={"settings": {"enabled": True}})
        ),
        patch.object(
            base_snapshot,
            "mark_base_snapshot_building",
            AsyncMock(return_value={"status": "building", "progress": {"phase": "starting"}}),
        ),
        patch.object(base_snapshot, "repos_to_preclone", AsyncMock(return_value=["acme/one"])),
        patch.object(base_snapshot, "_put_record", AsyncMock()),
        patch.object(base_snapshot, "_prune_old_snapshots", AsyncMock()),
        patch.object(base_snapshot, "_set_progress", AsyncMock()),
        patch(
            "agent.utils.github_app.get_github_app_installation_token",
            AsyncMock(return_value="ghs_token"),
        ),
        patch("agent.integrations.langsmith._configure_github_proxy", AsyncMock()),
        patch("agent.integrations.langsmith.get_async_sandbox_client", return_value=client),
    ):
        record = await base_snapshot.rebuild_base_snapshot()

    assert record["progress"] is None
    assert record["build_started_at"] is None


@pytest.mark.asyncio
async def test_clone_sweep_publishes_progress_per_repo() -> None:
    published: list[tuple[str, int, int]] = []

    async def _capture(phase: str, completed: int, total: int) -> None:
        published.append((phase, completed, total))

    async def _run(_command, timeout=None, on_stdout=lambda _chunk: None):
        on_stdout("ok acme/one\n")
        on_stdout("fail acme/two\n")
        # Long enough for at least one publisher tick.
        await asyncio.sleep(base_snapshot.PROGRESS_POLL_SECONDS * 1.5)
        return SimpleNamespace(stdout="ok acme/one\nfail acme/two\n", exit_code=0)

    sandbox = SimpleNamespace(run=_run)
    with patch.object(base_snapshot, "_set_progress", _capture):
        await base_snapshot._run_clone_sweep(
            sandbox, "/workspace", ["acme/one", "acme/two", "acme/three"]
        )

    assert published[0] == ("cloning", 0, 3)
    # Both a success and a failure count as progress: the bar tracks work done.
    assert ("cloning", 2, 3) in published


@pytest.mark.asyncio
async def test_rebuild_endpoint_rejects_a_concurrent_rebuild() -> None:
    record = {"settings": {"enabled": True}, "status": "building", "build_started_at": _iso(0)}
    with patch.object(routes, "get_base_snapshot_record", AsyncMock(return_value=record)):
        with pytest.raises(HTTPException) as exc:
            await routes.api_rebuild_base_snapshot(BackgroundTasks(), _admin={"sub": "octo"})
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_rebuild_endpoint_marks_building_before_returning() -> None:
    record = {"settings": {"enabled": True}, "status": "ready"}
    mark = AsyncMock(return_value={"status": "building"})
    with (
        patch.object(routes, "get_base_snapshot_record", AsyncMock(return_value=record)),
        patch.object(routes, "mark_base_snapshot_building", mark),
    ):
        result = await routes.api_rebuild_base_snapshot(BackgroundTasks(), _admin={"sub": "octo"})

    # Without this the UI's poll reads a stale "ready" and stops polling.
    mark.assert_awaited_once()
    assert result["status"] == "building"


def test_mirror_sweep_is_owner_scoped_and_fault_tolerant() -> None:
    command = build_mirror_sweep_script("/workspace", ["acme/one", "other/one"], proxy_auth=True)
    assert "/workspace/.repo-cache/acme/one" in command
    # Same repo name, different owners: owner-scoped paths keep them distinct.
    assert "/workspace/.repo-cache/other/one" in command
    # Working trees, not bare mirrors: taking one must be a rename, and baked
    # dependencies have to live somewhere.
    assert "--bare" not in command
    # One repo's failure must not abort the sweep.
    assert "set -e" not in command


def test_mirror_sweep_updates_an_existing_checkout_instead_of_recloning() -> None:
    command = build_mirror_sweep_script("/workspace", ["acme/one"], proxy_auth=True)
    assert "if [ -d /workspace/.repo-cache/acme/one/.git ]" in command
    assert "git fetch origin --prune" in command
    # A broken or diverged checkout still self-heals via a fresh clone.
    assert "git clone https://github.com/acme/one.git" in command


def test_mirror_sweep_prunes_repos_that_dropped_out() -> None:
    command = build_mirror_sweep_script("/workspace", ["acme/one"], proxy_auth=True)
    # Without this an incremental cache only ever grows.
    assert "KEEP=acme/one" in command
    assert "for dir in /workspace/.repo-cache/*/*" in command
    assert 'rm -rf "$dir"' in command


def test_mirror_sweep_only_uses_proxy_auth_when_asked() -> None:
    # LangSmith injects real credentials behind GH_TOKEN=dummy via its proxy.
    assert "GH_TOKEN=dummy git clone" in build_mirror_sweep_script(
        "/workspace", ["acme/one"], proxy_auth=True
    )
    # Elsewhere, no token is interpolated at all.
    plain = build_mirror_sweep_script("/workspace", ["acme/one"], proxy_auth=False)
    assert "GH_TOKEN" not in plain


def test_proxy_path_clones_with_git_only() -> None:
    # gh demands an authenticated login, and there is none in the sandbox; git
    # over https picks up the proxy's injected credentials.
    command = build_mirror_sweep_script("/workspace", ["acme/one"], proxy_auth=True)
    assert "gh repo clone" not in command
    assert "GH_TOKEN=dummy git clone" in command


def test_local_path_prefers_an_authenticated_gh() -> None:
    # Without a proxy, an authenticated gh is the only way a private repo
    # clones -- and it uses the developer's existing login rather than a token
    # interpolated into a command string.
    command = build_mirror_sweep_script("/workspace", ["acme/one"], proxy_auth=False)
    assert "gh auth status" in command
    assert "gh repo clone acme/one" in command
    # Still falls back to git, which covers every public repo.
    assert "|| git clone https://github.com/acme/one.git" in command
    assert "GH_TOKEN" not in command


def test_mirror_sweep_reports_why_a_clone_failed() -> None:
    # A red status with no diagnosis is not actionable.
    command = build_mirror_sweep_script("/workspace", ["acme/one"], proxy_auth=False)
    assert ".err" in command
    assert "fail acme/one $(" in command


def test_mirror_sweep_quotes_hostile_repo_names() -> None:
    command = build_mirror_sweep_script("/workspace", ["acme/a;rm -rf /"], proxy_auth=True)
    assert "'acme/a;rm -rf /'" in command
    assert "'/workspace/.repo-cache/acme/a;rm -rf /'" in command


def test_parse_mirror_sweep_output_buckets_results() -> None:
    cloned, failed = parse_mirror_sweep_output("ok acme/one\nfail acme/two\nnoise\n")
    assert cloned == ["acme/one"]
    assert failed == ["acme/two"]


@pytest.mark.asyncio
async def test_persistent_provider_warms_the_cache_without_a_snapshot() -> None:
    backend = SimpleNamespace(
        aexecute=AsyncMock(return_value=SimpleNamespace(output="ok acme/one\n", exit_code=0))
    )
    with (
        patch.dict("os.environ", {"SANDBOX_TYPE": "local"}),
        patch.object(
            base_snapshot, "_read_record", AsyncMock(return_value={"settings": {"enabled": True}})
        ),
        patch.object(base_snapshot, "mark_base_snapshot_building", AsyncMock(return_value={})),
        patch.object(base_snapshot, "repos_to_preclone", AsyncMock(return_value=["acme/one"])),
        patch.object(base_snapshot, "_put_record", AsyncMock()),
        patch.object(base_snapshot, "_set_progress", AsyncMock()),
        patch("agent.utils.sandbox.create_sandbox", AsyncMock(return_value=backend)),
        patch(
            "agent.utils.sandbox_paths.aresolve_sandbox_work_dir",
            AsyncMock(return_value="/sbx"),
        ),
    ):
        record = await base_snapshot.rebuild_base_snapshot()

    assert record["status"] == "ready"
    assert record["mode"] == "cache"
    # No image is involved, so runs keep booting the default snapshot.
    assert record["snapshot_id"] is None
    assert record["repos"] == ["acme/one"]
    assert "/sbx/.repo-cache" in backend.aexecute.await_args.args[0]


@pytest.mark.asyncio
async def test_ephemeral_provider_without_capture_fails_loudly() -> None:
    # Warming a throwaway box that is discarded moments later helps nobody, and
    # reporting success would be a lie.
    with (
        patch.dict("os.environ", {"SANDBOX_TYPE": "e2b"}),
        patch.object(
            base_snapshot, "_read_record", AsyncMock(return_value={"settings": {"enabled": True}})
        ),
        patch.object(base_snapshot, "mark_base_snapshot_building", AsyncMock(return_value={})),
        patch.object(base_snapshot, "repos_to_preclone", AsyncMock(return_value=["acme/one"])),
        patch.object(base_snapshot, "_put_record", AsyncMock()),
    ):
        record = await base_snapshot.rebuild_base_snapshot()

    assert record["status"] == "failed"
    assert "e2b" in record["status_message"]


@pytest.mark.asyncio
async def test_langsmith_rebuild_is_tagged_as_snapshot_mode() -> None:
    _sandbox, client = _builder_client()
    with (
        patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith", "DEFAULT_SANDBOX_SNAPSHOT_ID": "s"}),
        patch.object(
            base_snapshot, "_read_record", AsyncMock(return_value={"settings": {"enabled": True}})
        ),
        patch.object(base_snapshot, "mark_base_snapshot_building", AsyncMock(return_value={})),
        patch.object(base_snapshot, "repos_to_preclone", AsyncMock(return_value=["acme/one"])),
        patch.object(base_snapshot, "_put_record", AsyncMock()),
        patch.object(base_snapshot, "_prune_old_snapshots", AsyncMock()),
        patch.object(base_snapshot, "_set_progress", AsyncMock()),
        patch(
            "agent.utils.github_app.get_github_app_installation_token",
            AsyncMock(return_value="ghs_token"),
        ),
        patch("agent.integrations.langsmith._configure_github_proxy", AsyncMock()),
        patch("agent.integrations.langsmith.get_async_sandbox_client", return_value=client),
    ):
        record = await base_snapshot.rebuild_base_snapshot()

    assert record["mode"] == "snapshot"
    assert record["snapshot_id"] == "snap-new"


def test_builder_builds_on_the_previous_capture() -> None:
    existing = {
        "status": "ready",
        "mode": "snapshot",
        "snapshot_id": "snap-yesterday",
        "seed_snapshot_id": "seed-1",
    }
    assert (
        base_snapshot._builder_base_snapshot(existing, "seed-1", from_scratch=False)
        == "snap-yesterday"
    )


def test_builder_falls_back_to_seed_when_there_is_nothing_to_build_on() -> None:
    assert base_snapshot._builder_base_snapshot({}, "seed-1", from_scratch=False) == "seed-1"
    failed = {"status": "failed", "mode": "snapshot", "snapshot_id": "snap-bad"}
    assert base_snapshot._builder_base_snapshot(failed, "seed-1", from_scratch=False) == "seed-1"
    # A cache-mode record has no image to build on.
    cache_mode = {"status": "ready", "mode": "cache", "snapshot_id": None}
    assert (
        base_snapshot._builder_base_snapshot(cache_mode, "seed-1", from_scratch=False) == "seed-1"
    )


def test_builder_restarts_from_seed_when_the_seed_changes() -> None:
    # Otherwise a new base image (new tools, new runtimes) would never reach the
    # chain, because every build would keep descending from the old one.
    existing = {
        "status": "ready",
        "mode": "snapshot",
        "snapshot_id": "snap-yesterday",
        "seed_snapshot_id": "seed-old",
    }
    assert base_snapshot._builder_base_snapshot(existing, "seed-new", from_scratch=False) == (
        "seed-new"
    )


def test_from_scratch_discards_the_previous_capture() -> None:
    existing = {
        "status": "ready",
        "mode": "snapshot",
        "snapshot_id": "snap-yesterday",
        "seed_snapshot_id": "seed-1",
    }
    assert base_snapshot._builder_base_snapshot(existing, "seed-1", from_scratch=True) == "seed-1"


@pytest.mark.asyncio
async def test_rebuild_boots_the_builder_from_the_previous_capture() -> None:
    _sandbox, client = _builder_client()
    existing = {
        "settings": {"enabled": True},
        "status": "ready",
        "mode": "snapshot",
        "snapshot_id": "snap-yesterday",
        "seed_snapshot_id": "seed-1",
    }
    with (
        patch.dict(
            "os.environ", {"SANDBOX_TYPE": "langsmith", "DEFAULT_SANDBOX_SNAPSHOT_ID": "seed-1"}
        ),
        patch.object(base_snapshot, "_read_record", AsyncMock(return_value=existing)),
        patch.object(base_snapshot, "mark_base_snapshot_building", AsyncMock(return_value={})),
        patch.object(base_snapshot, "repos_to_preclone", AsyncMock(return_value=["acme/one"])),
        patch.object(base_snapshot, "_put_record", AsyncMock()),
        patch.object(base_snapshot, "_prune_old_snapshots", AsyncMock()),
        patch.object(base_snapshot, "_set_progress", AsyncMock()),
        patch(
            "agent.utils.github_app.get_github_app_installation_token",
            AsyncMock(return_value="ghs_token"),
        ),
        patch("agent.integrations.langsmith._configure_github_proxy", AsyncMock()),
        patch("agent.integrations.langsmith.get_async_sandbox_client", return_value=client),
    ):
        await base_snapshot.rebuild_base_snapshot()

    assert client.create_sandbox.await_args.kwargs["snapshot_id"] == "snap-yesterday"


@pytest.mark.asyncio
async def test_rebuild_endpoint_passes_from_scratch_through() -> None:
    record = {"settings": {"enabled": True}, "status": "ready"}
    tasks = BackgroundTasks()
    with (
        patch.object(routes, "get_base_snapshot_record", AsyncMock(return_value=record)),
        patch.object(routes, "mark_base_snapshot_building", AsyncMock(return_value={})),
    ):
        await routes.api_rebuild_base_snapshot(tasks, from_scratch=True, _admin={"sub": "octo"})

    assert tasks.tasks[0].args == (True,)


def test_scripts_are_length_limited() -> None:
    with pytest.raises(ValidationError):
        base_snapshot.BaseSnapshotSettings(pre_script="x" * (base_snapshot.SCRIPT_MAX_CHARS + 1))


@pytest.mark.asyncio
async def test_hooks_run_around_the_sweep_in_the_work_dir() -> None:
    calls: list[str] = []

    async def _exec(command: str, timeout: int) -> Any:
        calls.append(command)
        return SimpleNamespace(stdout="", exit_code=0)

    with patch.object(base_snapshot, "_set_progress", AsyncMock()):
        err = await base_snapshot._run_hook(_exec, "post_script", "pnpm install", "/workspace")

    assert err is None
    # cd first so a script can address .repo-cache relatively.
    assert calls == ["set -e\ncd /workspace\npnpm install"]


@pytest.mark.asyncio
async def test_empty_script_is_a_no_op() -> None:
    async def _exec(command: str, timeout: int) -> Any:
        raise AssertionError("should not run")

    assert await base_snapshot._run_hook(_exec, "pre_script", "   ", "/workspace") is None


@pytest.mark.asyncio
async def test_failing_hook_fails_the_build() -> None:
    # A half-installed snapshot is worse than no new snapshot: every run would
    # inherit the broken state and none would say why.
    async def _exec(command: str, timeout: int) -> Any:
        return SimpleNamespace(stdout="pnpm: command not found", exit_code=127)

    with patch.object(base_snapshot, "_set_progress", AsyncMock()):
        err = await base_snapshot._run_hook(_exec, "post_script", "pnpm install", "/workspace")

    assert err is not None
    assert "exited 127" in err
    assert "command not found" in err


@pytest.mark.asyncio
async def test_failing_post_script_blocks_the_capture() -> None:
    _sandbox, client = _builder_client()
    settings = {"enabled": True, "post_script": "exit 1"}
    with (
        patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        patch.object(base_snapshot, "_read_record", AsyncMock(return_value={"settings": settings})),
        patch.object(base_snapshot, "mark_base_snapshot_building", AsyncMock(return_value={})),
        patch.object(base_snapshot, "repos_to_preclone", AsyncMock(return_value=["acme/one"])),
        patch.object(base_snapshot, "_put_record", AsyncMock()),
        patch.object(base_snapshot, "_set_progress", AsyncMock()),
        patch.object(
            base_snapshot, "_run_hook", AsyncMock(side_effect=[None, "post_script exited 1"])
        ),
        patch(
            "agent.utils.github_app.get_github_app_installation_token",
            AsyncMock(return_value="ghs_token"),
        ),
        patch("agent.integrations.langsmith._configure_github_proxy", AsyncMock()),
        patch("agent.integrations.langsmith.get_async_sandbox_client", return_value=client),
    ):
        record = await base_snapshot.rebuild_base_snapshot()

    assert record["status"] == "failed"
    client.capture_snapshot.assert_not_awaited()


def test_mirror_sweep_keeps_scratch_files_out_of_the_cache() -> None:
    # Anything left inside the cache root is captured into the snapshot, and a
    # stray file breaks hooks that glob `.repo-cache/*/*` for repositories.
    command = build_mirror_sweep_script("/workspace", ["acme/one"], proxy_auth=False)
    assert "/workspace/.repo-cache/acme/one.err" not in command
    assert "/tmp/openswe-warm.err" in command
