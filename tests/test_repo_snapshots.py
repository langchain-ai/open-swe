from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.dashboard.repo_snapshots import (
    RepoSnapshotUpdate,
    create_repo_snapshot,
    generate_dockerfile_template,
    mark_repo_snapshot_building,
    resolve_repo_snapshot_id,
    run_snapshot_build,
    update_repo_snapshot,
)


def test_generate_dockerfile_template_uses_base_image() -> None:
    with patch.dict("os.environ", {"REPO_SNAPSHOT_BASE_IMAGE": "ghcr.io/acme/base:1"}):
        template = generate_dockerfile_template("acme/repo")
    assert "FROM ghcr.io/acme/base:1" in template
    assert "acme/repo" in template


@pytest.mark.asyncio
async def test_resolve_returns_snapshot_id_when_ready() -> None:
    with patch(
        "agent.dashboard.repo_snapshots._get_value",
        new_callable=AsyncMock,
        return_value={"status": "ready", "snapshot_id": "snap-123"},
    ):
        result = await resolve_repo_snapshot_id("acme", "repo")
    assert result == "snap-123"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_not_ready() -> None:
    with patch(
        "agent.dashboard.repo_snapshots._get_value",
        new_callable=AsyncMock,
        return_value={"status": "building", "snapshot_id": "snap-123"},
    ):
        result = await resolve_repo_snapshot_id("acme", "repo")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_returns_none_without_record() -> None:
    with patch(
        "agent.dashboard.repo_snapshots._get_value",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = await resolve_repo_snapshot_id("acme", "repo")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_returns_none_without_owner_or_name() -> None:
    assert await resolve_repo_snapshot_id(None, "repo") is None
    assert await resolve_repo_snapshot_id("acme", None) is None


@pytest.mark.asyncio
async def test_resolve_swallows_errors() -> None:
    with patch(
        "agent.dashboard.repo_snapshots._get_value",
        new_callable=AsyncMock,
        side_effect=RuntimeError("store down"),
    ):
        assert await resolve_repo_snapshot_id("acme", "repo") is None


@pytest.mark.asyncio
async def test_create_repo_snapshot_puts_new_record() -> None:
    mock_put = AsyncMock()
    with (
        patch(
            "agent.dashboard.repo_snapshots.get_repo_snapshot",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("agent.dashboard.repo_snapshots._client") as mock_client,
    ):
        mock_client.return_value.store.put_item = mock_put
        record = await create_repo_snapshot("acme/repo", "octo")
    assert record["full_name"] == "acme/repo"
    assert record["status"] == "none"
    assert "FROM" in record["dockerfile"]
    mock_put.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_repo_snapshot_persists_fields() -> None:
    mock_put = AsyncMock()
    with (
        patch(
            "agent.dashboard.repo_snapshots.get_repo_snapshot",
            new_callable=AsyncMock,
            return_value={"full_name": "acme/repo", "status": "none"},
        ),
        patch("agent.dashboard.repo_snapshots._client") as mock_client,
    ):
        mock_client.return_value.store.put_item = mock_put
        record = await update_repo_snapshot(
            "acme/repo",
            RepoSnapshotUpdate(dockerfile="FROM python:3.12-slim\n", vcpus=4),
        )
    assert record["dockerfile"] == "FROM python:3.12-slim\n"
    assert record["vcpus"] == 4
    mock_put.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_building_sets_status() -> None:
    mock_put = AsyncMock()
    with (
        patch(
            "agent.dashboard.repo_snapshots.get_repo_snapshot",
            new_callable=AsyncMock,
            return_value={"full_name": "acme/repo", "status": "ready"},
        ),
        patch("agent.dashboard.repo_snapshots._client") as mock_client,
    ):
        mock_client.return_value.store.put_item = mock_put
        record = await mark_repo_snapshot_building("acme/repo")
    assert record["status"] == "building"
    mock_put.assert_awaited_once()


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
    assert statuses[-1][1]["snapshot_id"] == "snap-new"


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


def test_create_langsmith_sandbox_uses_repo_snapshot_override() -> None:
    from agent.integrations import langsmith

    fake_backend = MagicMock()
    fake_backend.id = "box-1"
    provider = MagicMock()
    provider.get_or_create.return_value = fake_backend

    with (
        patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "env-default"}, clear=True),
        patch.object(langsmith, "LangSmithProvider", return_value=provider),
        patch.object(langsmith, "_update_thread_sandbox_metadata"),
    ):
        langsmith.create_langsmith_sandbox(snapshot_id="repo-snap")

    assert provider.get_or_create.call_args.kwargs["snapshot_id"] == "repo-snap"


def test_create_langsmith_sandbox_falls_back_to_default() -> None:
    from agent.integrations import langsmith

    fake_backend = MagicMock()
    fake_backend.id = "box-2"
    provider = MagicMock()
    provider.get_or_create.return_value = fake_backend

    with (
        patch.dict("os.environ", {"DEFAULT_SANDBOX_SNAPSHOT_ID": "env-default"}, clear=True),
        patch.object(langsmith, "LangSmithProvider", return_value=provider),
        patch.object(langsmith, "_update_thread_sandbox_metadata"),
    ):
        langsmith.create_langsmith_sandbox()

    assert provider.get_or_create.call_args.kwargs["snapshot_id"] == "env-default"
