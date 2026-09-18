"""The model-identity read endpoint resolves the policy for the caller's thread."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from agent.dashboard import workspace_settings
from agent.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    api_get_model_identity,
    upsert_instance_settings,
    upsert_workspace_overrides,
)
from tests.conftest import FakeStore


def _thread(workspace: str | None = None) -> AsyncMock:
    metadata: dict[str, Any] = {"workspace": workspace} if workspace else {}
    return AsyncMock(return_value=metadata)


async def test_workspace_query_resolves_that_workspaces_policy(
    fake_store: FakeStore,
) -> None:
    await upsert_instance_settings(WorkspaceSettingsUpdate(show_model_identity=True))
    await upsert_workspace_overrides("oss", WorkspaceSettingsUpdate(show_model_identity=False))

    visible = await api_get_model_identity(workspace="oss", thread=None, session={"sub": "alice"})
    assert visible == {"show_model_identity": False, "workspace": "oss"}

    default = await api_get_model_identity(thread=None, session={"sub": "alice"})
    assert default == {"show_model_identity": True, "workspace": "default"}


async def test_thread_query_uses_the_threads_workspace(fake_store: FakeStore) -> None:
    await upsert_instance_settings(WorkspaceSettingsUpdate(show_model_identity=True))
    await upsert_workspace_overrides("oss", WorkspaceSettingsUpdate(show_model_identity=False))

    with patch.object(workspace_settings, "authorized_thread_metadata", _thread("oss")):
        result = await api_get_model_identity(
            thread="thread-1", session={"sub": "alice", "email": "alice@example.com"}
        )
    assert result == {"show_model_identity": False, "workspace": "oss"}

    await upsert_workspace_overrides("core", WorkspaceSettingsUpdate(show_model_identity=True))
    with patch.object(workspace_settings, "authorized_thread_metadata", _thread("oss")):
        result = await api_get_model_identity(
            workspace="core",
            thread="thread-1",
            session={"sub": "alice", "email": "alice@example.com"},
        )
    assert result == {"show_model_identity": False, "workspace": "oss"}


async def test_a_thread_the_caller_cannot_read_refuses_the_lookup(
    fake_store: FakeStore,
) -> None:
    denied = AsyncMock(side_effect=HTTPException(404, "thread not found"))
    with (
        patch.object(workspace_settings, "authorized_thread_metadata", denied),
        pytest.raises(HTTPException) as refused,
    ):
        await api_get_model_identity(thread="thread-1", session={"sub": "bob"})
    assert refused.value.status_code == 404
