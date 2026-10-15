from typing import Any

import pytest

from openswe.jobs import cron_import, queue
from openswe.workspaces.store import WORKSPACES, WorkspaceCreate
from tests.conftest import MemoryEventClaims

pytestmark = pytest.mark.usefixtures("registry_db")


class _Crons:
    def __init__(self, crons: list[dict[str, Any]]) -> None:
        self.crons = crons
        self.deleted: list[str] = []

    async def search(self, *, limit: int, offset: int) -> list[dict[str, Any]]:
        live = [cron for cron in self.crons if cron["cron_id"] not in self.deleted]
        return live[offset : offset + limit]

    async def delete(self, cron_id: str) -> None:
        self.deleted.append(cron_id)


class _Client:
    def __init__(self, crons: list[dict[str, Any]]) -> None:
        self.crons = _Crons(crons)


def _cron(cron_id: str, schedule: str, **given: str) -> dict[str, Any]:
    return {"cron_id": cron_id, "schedule": schedule, "payload": {"input": given}, "metadata": {}}


async def test_scheduler_crons_move_to_jobs_once(
    monkeypatch: pytest.MonkeyPatch, memory_event_claims: MemoryEventClaims
) -> None:
    await WORKSPACES.create(WorkspaceCreate(name="Core"), "alice")
    await WORKSPACES.set_refresh_cron_id("core", "cron-refresh")
    await WORKSPACES.create(WorkspaceCreate(name="Docs"), "alice")
    await WORKSPACES.set_refresh_cron_id("docs", "cron-legacy")
    client = _Client(
        [
            _cron("cron-poll", "* * * * *", task="background_tasks", thread_id="thread-1"),
            _cron("cron-refresh", "0 3 * * *", task="workspace_refresh", workspace_slug="core"),
            _cron("cron-legacy", "0 4 * * *", task="environment_refresh", environment_slug="docs"),
            _cron("cron-retired", "0 * * * *", task="thread_feedback"),
            _cron("cron-unknown", "0 * * * *", task="something_new"),
            {**_cron("cron-wakeup", "5 4 3 2 *"), "thread_id": "thread-2"},
        ]
    )
    monkeypatch.setattr(cron_import, "get_client", lambda: client)

    assert await cron_import.import_agent_server_crons() == 3

    poll = await queue.get("background_tasks:thread-1")
    refresh = await queue.get("workspace_refresh:core")
    assert poll is not None and poll.cron == "* * * * *"
    assert refresh is not None
    workspace = await WORKSPACES.get("core")
    assert workspace is not None and workspace.refresh_cron_id == str(refresh.id)
    assert await queue.get("workspace_refresh:docs") is not None
    # The cron nothing replaces stays, firing as before, for the next startup to retry.
    assert client.crons.deleted == ["cron-poll", "cron-refresh", "cron-legacy", "cron-retired"]

    memory_event_claims.held.clear()
    assert await cron_import.import_agent_server_crons() == 0
    assert len(await queue.find({})) == 3
