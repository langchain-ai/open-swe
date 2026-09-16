from typing import Any

from agent.run_config import Repo
from agent.webhooks import common
from agent.workspaces import routing
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


async def test_workspace_for_repos_takes_the_first_owned_one_then_the_default(
    registry_db: None,
) -> None:
    repos = [Repo(owner="acme", name="unowned"), Repo(owner="acme", name="oss")]
    assert await common.workspace_for_repos(repos) == "default"

    await WORKSPACES.create(WorkspaceCreate(name="OSS", repos=["acme/oss"]), "alice")

    assert await common.workspace_for_repos(repos) == "oss"
    assert await common.workspace_for_repos([]) == "default"


async def test_workspace_for_repos_falls_back_when_the_store_fails(monkeypatch) -> None:
    """An unreadable binding must not stop a run, only misroute it loudly."""

    async def unreadable(full_name: str) -> str | None:
        raise RuntimeError("store is down")

    monkeypatch.setattr(routing.WORKSPACES, "owner_of_repo", unreadable)

    assert await common.workspace_for_repos([Repo(owner="acme", name="oss")]) == "default"
