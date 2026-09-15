from typing import Any

from agent.webhooks import common
from agent.workspaces.store import WORKSPACES, WorkspaceCreate


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


async def test_workspace_for_repo_config_resolves_owner_and_falls_back_to_default(
    fake_store: Any,
) -> None:
    repo_config = {"owner": "acme", "name": "oss"}
    assert await common.workspace_for_repo_config(repo_config) == "default"

    await WORKSPACES.create(WorkspaceCreate(name="OSS", repos=["acme/oss"]), "alice")

    assert await common.workspace_for_repo_config(repo_config) == "oss"
