from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe.source_context import SourceContext
from openswe.threads.index import IndexedThread
from openswe.webhooks import common
from openswe.workspaces import routing
from openswe.workspaces.store import WORKSPACES, WorkspaceCreate


class _Client:
    def __init__(self, metadata: dict[str, Any]) -> None:
        self._metadata = metadata

    @property
    def threads(self) -> _Client:
        return self

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": self._metadata}


async def test_get_thread_workspace_prefers_workspace_then_environment(monkeypatch) -> None:
    monkeypatch.setattr(
        common, "get_client", lambda url=None: _Client({"workspace": "oss", "environment": "old"})
    )
    assert await common.get_thread_workspace("t1") == "oss"
    monkeypatch.setattr(common, "get_client", lambda url=None: _Client({"environment": "old"}))
    assert await common.get_thread_workspace("t1") == "old"
    monkeypatch.setattr(common, "get_client", lambda url=None: _Client({}))
    assert await common.get_thread_workspace("t1") is None


@pytest.mark.parametrize("failure", ["read", "scope", "private", "owned-stub", "update-race"])
async def test_public_only_metadata_never_overwrites_an_unauthorized_thread(monkeypatch, failure):
    now = datetime.now(UTC)
    context = SourceContext.parse(
        {"slack_thread": {"team_id": "T1", "channel_id": "C1", "thread_ts": "1.0"}}
    )
    metadata = {
        "visibility": "public",
        "owner_type": "user",
        "owner_login": "alice",
        "source_context": context.dump(),
    }
    if failure == "scope":
        metadata["source_context"] = SourceContext.parse(
            {"slack_thread": {"team_id": "T1", "channel_id": "C2", "thread_ts": "1.0"}}
        ).dump()
    elif failure == "private":
        metadata["visibility"] = "private"
    elif failure == "owned-stub":
        metadata[common.PROVISIONAL_VISIBILITY_KEY] = True
    snapshot = IndexedThread(
        thread_id="thread-1",
        runtime="mda",
        metadata=metadata,
        status="idle",
        created_at=now,
        updated_at=now,
    )
    read = AsyncMock(return_value=snapshot)
    if failure == "read":
        read.side_effect = RuntimeError("offline")
    write = AsyncMock(return_value=None)
    monkeypatch.setattr(common.THREADS, "get", read)
    monkeypatch.setattr(common.THREADS, "update_if_current", write)
    monkeypatch.setattr(common, "participant_metadata", AsyncMock(return_value={}))
    monkeypatch.setattr(common.User, "login_for_slack", AsyncMock(return_value=None))
    monkeypatch.setattr(common, "get_slack_permalink", AsyncMock(return_value=""))
    assert not await common.upsert_agent_thread_metadata(
        "thread-1",
        source="slack",
        title="Question",
        source_context=context,
        owner_type="system",
        public_only=True,
    )
    assert snapshot.metadata == metadata
    if failure == "update-race":
        write.assert_awaited_once()
        assert "owner_type" not in write.call_args.kwargs["metadata"]
        assert "owner_login" not in write.call_args.kwargs["metadata"]
    else:
        write.assert_not_awaited()


async def test_workspace_for_repo_config_resolves_owner_and_falls_back_to_default(
    registry_db: None,
) -> None:
    repo_config = {"owner": "acme", "name": "oss"}
    assert await common.workspace_for_repo_config(repo_config) == "default"

    await WORKSPACES.create(WorkspaceCreate(name="OSS", repos=["acme/oss"]), "alice")

    assert await common.workspace_for_repo_config(repo_config) == "oss"


async def test_workspace_for_repo_config_falls_back_when_the_store_fails(monkeypatch) -> None:
    """An unreadable binding must not stop a run, only misroute it loudly."""

    async def unreadable(full_name: str) -> str | None:
        raise RuntimeError("store is down")

    monkeypatch.setattr(routing.WORKSPACES, "owner_of_repo", unreadable)

    assert await common.workspace_for_repo_config({"owner": "acme", "name": "oss"}) == "default"
