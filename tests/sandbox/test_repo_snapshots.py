import contextlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

from agent.dashboard.repo_snapshots import (
    RepoSnapshotConfigError,
    RepoSnapshotUpdate,
    create_repo_snapshot,
    generate_dockerfile_template,
    is_repo_snapshot_build_stale,
    mark_repo_snapshot_building,
    resolve_repo_snapshot_id,
    run_snapshot_build,
    update_repo_snapshot,
)
from agent.dashboard.routes import repo_snapshots as repo_snapshot_routes


def test_generate_dockerfile_template_uses_base_image() -> None:
    with patch.dict("os.environ", {"REPO_SNAPSHOT_BASE_IMAGE": "ghcr.io/acme/base:1"}):
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
        repo_snapshot_routes,
        "generate_dockerfile_template",
        side_effect=RepoSnapshotConfigError("base image missing"),
    ):
        with pytest.raises(HTTPException) as exc:
            await repo_snapshot_routes.api_repo_snapshot_template(
                "acme/repo", _admin={"sub": "octo"}
            )
    assert exc.value.status_code == 500
    assert "base image missing" in exc.value.detail


@pytest.mark.asyncio
async def test_create_endpoint_returns_configuration_error() -> None:
    body = repo_snapshot_routes.RepoSnapshotCreate(full_name="acme/repo")
    with patch.object(
        repo_snapshot_routes,
        "create_repo_snapshot",
        new_callable=AsyncMock,
        side_effect=RepoSnapshotConfigError("base image missing"),
    ):
        with pytest.raises(HTTPException) as exc:
            await repo_snapshot_routes.api_create_repo_snapshot(body, _admin={"sub": "octo"})
    assert exc.value.status_code == 500
    assert "base image missing" in exc.value.detail


def _fake_store_client(stored: dict[str, object] | None) -> MagicMock:
    client = MagicMock()
    client.store.get_item = AsyncMock(return_value={"value": stored} if stored else None)
    client.store.put_item = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_resolve_returns_snapshot_id_when_ready() -> None:
    client = _fake_store_client({"status": "ready", "snapshot_id": "snap-123"})
    with patch("agent.store.store_client", return_value=client):
        assert await resolve_repo_snapshot_id("acme", "repo") == "snap-123"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_not_ready() -> None:
    client = _fake_store_client({"status": "building", "snapshot_id": "snap-123"})
    with patch("agent.store.store_client", return_value=client):
        assert await resolve_repo_snapshot_id("acme", "repo") is None


@pytest.mark.asyncio
async def test_resolve_returns_none_without_record() -> None:
    client = _fake_store_client(None)
    with patch("agent.store.store_client", return_value=client):
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
async def test_create_repo_snapshot_puts_new_record() -> None:
    client = _fake_store_client(None)
    with (
        patch.dict("os.environ", {"REPO_SNAPSHOT_BASE_IMAGE": "ghcr.io/acme/base:1"}),
        patch("agent.store.store_client", return_value=client),
    ):
        record = await create_repo_snapshot("acme/repo", "octo")
    assert record["full_name"] == "acme/repo"
    assert record["status"] == "none"
    assert "FROM" in record["dockerfile"]
    client.store.put_item.assert_awaited_once_with(["repo_snapshots"], "acme/repo", record)


@pytest.mark.asyncio
async def test_update_repo_snapshot_persists_fields() -> None:
    client = _fake_store_client({"full_name": "acme/repo", "status": "none"})
    with patch("agent.store.store_client", return_value=client):
        record = await update_repo_snapshot(
            "acme/repo",
            RepoSnapshotUpdate(dockerfile="FROM python:3.12-slim\n", vcpus=4),
        )
    assert record["dockerfile"] == "FROM python:3.12-slim\n"
    assert record["vcpus"] == 4
    client.store.put_item.assert_awaited_once_with(["repo_snapshots"], "acme/repo", record)


@pytest.mark.asyncio
async def test_mark_building_sets_status() -> None:
    client = _fake_store_client({"full_name": "acme/repo", "status": "ready"})
    with patch("agent.store.store_client", return_value=client):
        record = await mark_repo_snapshot_building("acme/repo")
    assert record["status"] == "building"
    assert record["build_started_at"]
    client.store.put_item.assert_awaited_once_with(["repo_snapshots"], "acme/repo", record)


def test_building_record_without_started_at_is_stale() -> None:
    assert is_repo_snapshot_build_stale({"status": "building"}) is True


def test_recent_building_record_is_not_stale() -> None:
    record = {"status": "building", "build_started_at": datetime.now(UTC).isoformat()}
    assert is_repo_snapshot_build_stale(record) is False


def test_old_building_record_is_stale() -> None:
    started = datetime.now(UTC) - timedelta(hours=7)
    record = {"status": "building", "build_started_at": started.isoformat()}
    assert is_repo_snapshot_build_stale(record) is True


@pytest.mark.asyncio
async def test_build_endpoint_blocks_non_stale_build() -> None:
    record = {
        "full_name": "acme/repo",
        "status": "building",
        "dockerfile": "FROM x",
        "build_started_at": datetime.now(UTC).isoformat(),
    }
    with patch.object(
        repo_snapshot_routes, "get_repo_snapshot", new_callable=AsyncMock, return_value=record
    ):
        with pytest.raises(HTTPException) as exc:
            await repo_snapshot_routes.api_build_repo_snapshot(
                "acme/repo", BackgroundTasks(), _admin={"sub": "octo"}
            )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_build_endpoint_allows_stale_build_retry() -> None:
    stale_started = (datetime.now(UTC) - timedelta(hours=7)).isoformat()
    stale = {
        "full_name": "acme/repo",
        "status": "building",
        "dockerfile": "FROM x",
        "build_started_at": stale_started,
    }
    building = {**stale, "build_started_at": datetime.now(UTC).isoformat()}
    with (
        patch.object(
            repo_snapshot_routes, "get_repo_snapshot", new_callable=AsyncMock, return_value=stale
        ),
        patch.object(
            repo_snapshot_routes,
            "mark_repo_snapshot_building",
            new_callable=AsyncMock,
            return_value=building,
        ) as mark_building,
    ):
        result = await repo_snapshot_routes.api_build_repo_snapshot(
            "acme/repo", BackgroundTasks(), _admin={"sub": "octo"}
        )
    assert result is building
    mark_building.assert_awaited_once_with("acme/repo")


@pytest.mark.asyncio
async def test_run_snapshot_build_success_marks_ready() -> None:
    statuses: list[tuple[str, dict | None]] = []

    async def fake_set_status(full_name, status, *, status_message=None, extra=None):
        statuses.append((status, extra))

    with (
        patch(
            "agent.dashboard.repo_snapshots.get_repo_snapshot",
            new_callable=AsyncMock,
            return_value={"full_name": "acme/repo", "dockerfile": "FROM x"},
        ),
        patch(
            "agent.dashboard.repo_snapshots._build_snapshot_sync",
            return_value=("snap-new", "build log"),
        ),
        patch("agent.dashboard.repo_snapshots._set_status", side_effect=fake_set_status),
    ):
        await run_snapshot_build("acme/repo")

    assert statuses[-1][0] == "ready"
    extra = statuses[-1][1]
    assert extra is not None
    assert extra["snapshot_id"] == "snap-new"


@pytest.mark.asyncio
async def test_run_snapshot_build_failure_marks_failed() -> None:
    statuses: list[str] = []

    async def fake_set_status(full_name, status, *, status_message=None, extra=None):
        statuses.append(status)

    with (
        patch(
            "agent.dashboard.repo_snapshots.get_repo_snapshot",
            new_callable=AsyncMock,
            return_value={"full_name": "acme/repo", "dockerfile": "FROM x"},
        ),
        patch(
            "agent.dashboard.repo_snapshots._build_snapshot_sync",
            side_effect=RuntimeError("boom"),
        ),
        patch("agent.dashboard.repo_snapshots._set_status", side_effect=fake_set_status),
    ):
        await run_snapshot_build("acme/repo")

    assert statuses[-1] == "failed"


def _langsmith_create_patches(create_with_retry: AsyncMock):
    """Everything ``LangSmithProvider.create`` touches outside the create call itself."""
    from agent.integrations import langsmith

    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return (
        patch.dict(
            "os.environ",
            {"DEFAULT_SANDBOX_SNAPSHOT_ID": "env-default", "LANGSMITH_API_KEY": "ls-key"},
            clear=True,
        ),
        patch.object(langsmith, "AsyncSandboxClient", return_value=client),
        patch.object(langsmith, "_install_create_extra_fields"),
        patch.object(langsmith, "_create_sandbox_with_retry", create_with_retry),
        patch.object(langsmith, "TimeoutLangSmithSandbox", MagicMock(return_value="backend")),
    )


async def test_langsmith_create_uses_repo_snapshot_override() -> None:
    from agent.integrations.langsmith import LangSmithProvider

    create_with_retry = AsyncMock(return_value=MagicMock())
    with contextlib.ExitStack() as stack:
        for context in _langsmith_create_patches(create_with_retry):
            stack.enter_context(context)
        await LangSmithProvider().create(snapshot_id="repo-snap")

    assert create_with_retry.await_args_list[0].kwargs["snapshot_id"] == "repo-snap"


async def test_langsmith_create_falls_back_to_the_default_snapshot() -> None:
    from agent.integrations.langsmith import LangSmithProvider

    create_with_retry = AsyncMock(return_value=MagicMock())
    with contextlib.ExitStack() as stack:
        for context in _langsmith_create_patches(create_with_retry):
            stack.enter_context(context)
        await LangSmithProvider().create()

    assert create_with_retry.await_args_list[0].kwargs["snapshot_id"] == "env-default"


async def test_langsmith_create_without_any_snapshot_is_refused() -> None:
    from agent.integrations.langsmith import LangSmithProvider

    with patch.dict("os.environ", {"LANGSMITH_API_KEY": "ls-key"}, clear=True):
        with pytest.raises(ValueError, match="No base snapshot configured"):
            await LangSmithProvider().create()
