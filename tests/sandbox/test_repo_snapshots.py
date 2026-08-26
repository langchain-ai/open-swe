from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

from agent.dashboard import routes
from agent.dashboard.repo_snapshots import (
    REPO_SNAPSHOTS,
    REPO_SNAPSHOTS_NAMESPACE,
    RepoSnapshot,
    RepoSnapshotConfigError,
    RepoSnapshotUpdate,
    generate_dockerfile_template,
    resolve_repo_snapshot_id,
    run_snapshot_build,
)
from tests.conftest import FakeStore

_BASE_IMAGE = {"REPO_SNAPSHOT_BASE_IMAGE": "ghcr.io/acme/base:1"}


def test_generate_dockerfile_template_uses_base_image() -> None:
    with patch.dict("os.environ", _BASE_IMAGE):
        template = generate_dockerfile_template("acme/repo")
    assert "FROM ghcr.io/acme/base:1" in template
    assert "acme/repo" in template


def test_generate_dockerfile_template_requires_base_image() -> None:
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RepoSnapshotConfigError, match="REPO_SNAPSHOT_BASE_IMAGE"):
            generate_dockerfile_template("acme/repo")


@pytest.mark.asyncio
async def test_template_endpoint_returns_configuration_error() -> None:
    with patch.object(
        routes,
        "generate_dockerfile_template",
        side_effect=RepoSnapshotConfigError("base image missing"),
    ):
        with pytest.raises(HTTPException) as exc:
            await routes.api_repo_snapshot_template("acme/repo", _admin={"sub": "octo"})
    assert exc.value.status_code == 500
    assert "base image missing" in exc.value.detail


@pytest.mark.asyncio
async def test_create_endpoint_returns_configuration_error() -> None:
    body = routes.RepoSnapshotCreate(full_name="acme/repo")
    with patch.object(
        REPO_SNAPSHOTS,
        "create",
        new_callable=AsyncMock,
        side_effect=RepoSnapshotConfigError("base image missing"),
    ):
        with pytest.raises(HTTPException) as exc:
            await routes.api_create_repo_snapshot(body, _admin={"sub": "octo"})
    assert exc.value.status_code == 500
    assert "base image missing" in exc.value.detail


@pytest.mark.asyncio
async def test_resolve_returns_snapshot_id_when_ready(fake_store: FakeStore) -> None:
    fake_store.seed(
        REPO_SNAPSHOTS_NAMESPACE,
        "acme/repo",
        {"full_name": "acme/repo", "status": "ready", "snapshot_id": "snap-123"},
    )
    assert await resolve_repo_snapshot_id("acme", "repo") == "snap-123"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_not_ready(fake_store: FakeStore) -> None:
    fake_store.seed(
        REPO_SNAPSHOTS_NAMESPACE,
        "acme/repo",
        {"full_name": "acme/repo", "status": "building", "snapshot_id": "snap-123"},
    )
    assert await resolve_repo_snapshot_id("acme", "repo") is None


@pytest.mark.asyncio
async def test_resolve_returns_none_without_record(fake_store: FakeStore) -> None:
    assert await resolve_repo_snapshot_id("acme", "repo") is None


@pytest.mark.asyncio
async def test_resolve_returns_none_without_owner_or_name() -> None:
    assert await resolve_repo_snapshot_id(None, "repo") is None
    assert await resolve_repo_snapshot_id("acme", None) is None


@pytest.mark.asyncio
async def test_resolve_swallows_errors() -> None:
    client = MagicMock()
    client.store.get_item = AsyncMock(side_effect=RuntimeError("store down"))
    with patch("agent.store.store_client", return_value=client):
        assert await resolve_repo_snapshot_id("acme", "repo") is None


@pytest.mark.asyncio
async def test_create_repo_snapshot_puts_new_record(fake_store: FakeStore) -> None:
    with patch.dict("os.environ", _BASE_IMAGE):
        record = await REPO_SNAPSHOTS.create("acme/repo", "octo")

    assert record.full_name == "acme/repo"
    assert record.owner == "acme"
    assert record.status == "none"
    assert "FROM" in record.dockerfile
    assert fake_store.values(REPO_SNAPSHOTS_NAMESPACE)["acme/repo"] == record.model_dump(
        mode="json"
    )


@pytest.mark.asyncio
async def test_create_repo_snapshot_is_idempotent(fake_store: FakeStore) -> None:
    with patch.dict("os.environ", _BASE_IMAGE):
        first = await REPO_SNAPSHOTS.create("acme/repo", "octo")
        second = await REPO_SNAPSHOTS.create("acme/repo", "someone-else")
    assert second == first


@pytest.mark.asyncio
async def test_update_repo_snapshot_persists_fields(fake_store: FakeStore) -> None:
    fake_store.seed(
        REPO_SNAPSHOTS_NAMESPACE, "acme/repo", {"full_name": "acme/repo", "status": "none"}
    )

    record = await REPO_SNAPSHOTS.apply_update(
        "acme/repo",
        RepoSnapshotUpdate(dockerfile="FROM python:3.12-slim\n", vcpus=4),
    )

    assert record.dockerfile == "FROM python:3.12-slim\n"
    assert record.vcpus == 4
    assert (await REPO_SNAPSHOTS.get("acme/repo")) == record


@pytest.mark.asyncio
async def test_update_leaves_unspecified_sizing_alone(fake_store: FakeStore) -> None:
    fake_store.seed(
        REPO_SNAPSHOTS_NAMESPACE,
        "acme/repo",
        {"full_name": "acme/repo", "status": "none", "mem_bytes": 4 * 1024**3},
    )

    record = await REPO_SNAPSHOTS.apply_update("acme/repo", RepoSnapshotUpdate(dockerfile="FROM x"))

    assert record.mem_bytes == 4 * 1024**3


@pytest.mark.asyncio
async def test_mark_building_sets_status(fake_store: FakeStore) -> None:
    fake_store.seed(
        REPO_SNAPSHOTS_NAMESPACE, "acme/repo", {"full_name": "acme/repo", "status": "ready"}
    )

    record = await REPO_SNAPSHOTS.mark_building("acme/repo")

    assert record.status == "building"
    assert record.build_started_at
    assert (await REPO_SNAPSHOTS.get("acme/repo")) == record


@pytest.mark.asyncio
async def test_mark_building_rejects_missing_record(fake_store: FakeStore) -> None:
    with pytest.raises(ValueError, match="no repo snapshot record"):
        await REPO_SNAPSHOTS.mark_building("acme/repo")


def test_building_record_without_started_at_is_stale() -> None:
    assert RepoSnapshot(full_name="acme/repo", status="building").build_is_stale is True


def test_recent_building_record_is_not_stale() -> None:
    record = RepoSnapshot(
        full_name="acme/repo", status="building", build_started_at=datetime.now(UTC).isoformat()
    )
    assert record.build_is_stale is False


def test_old_building_record_is_stale() -> None:
    started = datetime.now(UTC) - timedelta(hours=7)
    record = RepoSnapshot(
        full_name="acme/repo", status="building", build_started_at=started.isoformat()
    )
    assert record.build_is_stale is True


def test_non_building_record_is_never_stale() -> None:
    assert RepoSnapshot(full_name="acme/repo", status="ready").build_is_stale is False


@pytest.mark.asyncio
async def test_build_endpoint_blocks_non_stale_build(fake_store: FakeStore) -> None:
    fake_store.seed(
        REPO_SNAPSHOTS_NAMESPACE,
        "acme/repo",
        {
            "full_name": "acme/repo",
            "status": "building",
            "dockerfile": "FROM x",
            "build_started_at": datetime.now(UTC).isoformat(),
        },
    )

    with pytest.raises(HTTPException) as exc:
        await routes.api_build_repo_snapshot("acme/repo", BackgroundTasks(), _admin={"sub": "octo"})

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_build_endpoint_rejects_empty_dockerfile(fake_store: FakeStore) -> None:
    fake_store.seed(
        REPO_SNAPSHOTS_NAMESPACE,
        "acme/repo",
        {"full_name": "acme/repo", "status": "none", "dockerfile": "   "},
    )

    with pytest.raises(HTTPException) as exc:
        await routes.api_build_repo_snapshot("acme/repo", BackgroundTasks(), _admin={"sub": "octo"})

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_build_endpoint_allows_stale_build_retry(fake_store: FakeStore) -> None:
    stale_started = (datetime.now(UTC) - timedelta(hours=7)).isoformat()
    fake_store.seed(
        REPO_SNAPSHOTS_NAMESPACE,
        "acme/repo",
        {
            "full_name": "acme/repo",
            "status": "building",
            "dockerfile": "FROM x",
            "build_started_at": stale_started,
        },
    )

    result = await routes.api_build_repo_snapshot(
        "acme/repo", BackgroundTasks(), _admin={"sub": "octo"}
    )

    assert result.status == "building"
    assert result.build_started_at != stale_started


@pytest.mark.asyncio
async def test_run_snapshot_build_success_marks_ready(fake_store: FakeStore) -> None:
    fake_store.seed(
        REPO_SNAPSHOTS_NAMESPACE,
        "acme/repo",
        {"full_name": "acme/repo", "dockerfile": "FROM x", "status": "building"},
    )

    with patch(
        "agent.dashboard.repo_snapshots._build_snapshot_sync",
        return_value=("snap-new", "build log"),
    ):
        await run_snapshot_build("acme/repo")

    record = await REPO_SNAPSHOTS.get("acme/repo")
    assert record is not None
    assert record.status == "ready"
    assert record.snapshot_id == "snap-new"
    assert record.build_log == "build log"
    assert record.build_started_at is None
    assert record.last_built_at


@pytest.mark.asyncio
async def test_run_snapshot_build_failure_marks_failed(fake_store: FakeStore) -> None:
    fake_store.seed(
        REPO_SNAPSHOTS_NAMESPACE,
        "acme/repo",
        {"full_name": "acme/repo", "dockerfile": "FROM x", "status": "building"},
    )

    with patch(
        "agent.dashboard.repo_snapshots._build_snapshot_sync",
        side_effect=RuntimeError("boom"),
    ):
        await run_snapshot_build("acme/repo")

    record = await REPO_SNAPSHOTS.get("acme/repo")
    assert record is not None
    assert record.status == "failed"
    assert record.status_message == "boom"
    assert record.build_started_at is None


async def test_create_langsmith_sandbox_uses_repo_snapshot_override() -> None:
    from agent.integrations import langsmith

    fake_backend = MagicMock()
    fake_backend.id = "box-1"
    provider = MagicMock()
    provider.get_or_create = AsyncMock(return_value=fake_backend)

    with (
        patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "env-default"}, clear=True),
        patch.object(langsmith, "LangSmithProvider", return_value=provider),
    ):
        await langsmith.create_langsmith_sandbox(snapshot_id="repo-snap")

    assert provider.get_or_create.call_args.kwargs["snapshot_id"] == "repo-snap"


async def test_create_langsmith_sandbox_falls_back_to_default() -> None:
    from agent.integrations import langsmith

    fake_backend = MagicMock()
    fake_backend.id = "box-2"
    provider = MagicMock()
    provider.get_or_create = AsyncMock(return_value=fake_backend)

    with (
        patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "env-default"}, clear=True),
        patch.object(langsmith, "LangSmithProvider", return_value=provider),
    ):
        await langsmith.create_langsmith_sandbox()

    assert provider.get_or_create.call_args.kwargs["snapshot_id"] == "env-default"
