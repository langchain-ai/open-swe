"""Machine callers on the one command endpoint the dashboard also uses."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException

from agent.api_keys.models import ApiKey
from agent.federation.github_oidc import GitHubActionsClaims
from agent.threads import runs
from agent.threads.callers import Caller
from agent.workspaces.store import WORKSPACES, WorkspaceCreate


class _FakeThreads:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> None:
        self.created.append(kwargs)

    async def get(self, thread_id: str) -> dict[str, Any]:
        record = next(item for item in self.created if item["thread_id"] == thread_id)
        return {"thread_id": thread_id, "status": "idle", "metadata": record["metadata"]}


class _FakeClient:
    def __init__(self) -> None:
        self.threads = _FakeThreads()


def _command(prompt: str, **configurable: Any) -> dict[str, Any]:
    return {
        "id": 1,
        "method": "run.start",
        "params": {
            "input": {"messages": [{"type": "human", "content": prompt}]},
            "config": {"configurable": configurable},
        },
    }


@pytest.fixture
async def machine(monkeypatch: pytest.MonkeyPatch, registry_db: None) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(runs, "langgraph_client", lambda: client)

    async def fake_repo_access(full_name: str) -> str:
        return "workspace-app-token"

    monkeypatch.setattr(runs, "require_repo_access_for_workspace", fake_repo_access)
    await WORKSPACES.create(WorkspaceCreate(name="core", repos=["acme/api"]), "admin")
    await WORKSPACES.create(WorkspaceCreate(name="oss", repos=["acme/oss"]), "admin")
    return client


async def _key_caller(workspace: str = "core") -> Caller:
    workspace_id = await WORKSPACES.id_for_slug(workspace)
    assert workspace_id is not None
    key, _ = await ApiKey.create(
        workspace_id=workspace_id,
        workspace=workspace,
        name="release CI",
        expires_at=datetime.now(UTC) + timedelta(days=30),
        created_by="admin",
    )
    return Caller.of_key(key)


def _workflow_caller(repository: str = "acme/api", workspace: str = "core") -> Caller:
    return Caller.of_workflow(
        GitHubActionsClaims(
            sub=f"repo:{repository}:ref:refs/heads/main",
            repository=repository,
            repository_owner=repository.split("/", 1)[0],
            workflow_ref=f"{repository}/.github/workflows/nightly.yml@refs/heads/main",
        ),
        workspace,
    )


async def test_a_key_starts_a_system_thread_with_no_person_on_it(machine: _FakeClient) -> None:
    caller = await _key_caller()

    enriched = await runs._enrich_system_run_start_command(
        "thread-1",
        caller,
        _command("Upgrade the linter", thread_type="system", repo="acme/api"),
        metadata={},
        creating=True,
    )

    metadata = machine.threads.created[0]["metadata"]
    assert metadata["source"] == "api"
    assert metadata["owner_type"] == "system"
    assert metadata["visibility"] == "public"
    assert metadata["workspace"] == "core"
    assert metadata["created_by"] == "admin"
    assert metadata["started_by_name"] == "release CI"
    assert (metadata["repo_owner"], metadata["repo_name"]) == ("acme", "api")
    assert "owner_login" not in metadata

    configurable = enriched["params"]["config"]["configurable"]
    assert configurable["source"] == "api"
    assert configurable["workspace"] == configurable["environment"] == "core"
    assert configurable["repo"] == {"owner": "acme", "name": "api"}
    assert configurable["invocation_id"]
    assert "github_login" not in configurable
    assert "user_email" not in configurable
    assert "admin_thread" not in configurable


async def test_a_workflow_defaults_to_its_own_repository(machine: _FakeClient) -> None:
    enriched = await runs._enrich_system_run_start_command(
        "thread-2",
        _workflow_caller(),
        _command("Fix the nightly", thread_type="system"),
        metadata={},
        creating=True,
    )

    configurable = enriched["params"]["config"]["configurable"]
    assert configurable["repo"] == {"owner": "acme", "name": "api"}
    assert machine.threads.created[0]["metadata"]["started_by_id"] == "github_actions:acme/api"


async def test_a_repository_in_another_workspace_is_refused(machine: _FakeClient) -> None:
    caller = await _key_caller()

    with pytest.raises(HTTPException) as refused:
        await runs._enrich_system_run_start_command(
            "thread-3",
            caller,
            _command("Upgrade the linter", thread_type="system", repo="acme/oss"),
            metadata={},
            creating=True,
        )

    assert refused.value.status_code == 403
    assert machine.threads.created == []


@pytest.mark.parametrize("requested", ["workspace", "private"])
async def test_a_machine_cannot_start_a_person_thread(machine: _FakeClient, requested: str) -> None:
    caller = await _key_caller()

    with pytest.raises(HTTPException) as refused:
        await runs._enrich_system_run_start_command(
            "thread-4",
            caller,
            _command("Upgrade the linter", thread_type=requested),
            metadata={},
            creating=True,
        )

    assert refused.value.status_code == 403
    assert machine.threads.created == []


async def test_the_thread_kind_has_to_be_named(machine: _FakeClient) -> None:
    caller = await _key_caller()

    with pytest.raises(HTTPException) as refused:
        await runs._enrich_system_run_start_command(
            "thread-5",
            caller,
            _command("Upgrade the linter"),
            metadata={},
            creating=True,
        )

    assert refused.value.status_code == 422


def test_only_an_admin_may_ask_for_a_system_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin")

    Caller.of_login("admin").authorize("system")
    Caller.of_login("intern").authorize("workspace")
    Caller.of_login("intern").authorize("private")

    with pytest.raises(HTTPException) as refused:
        Caller.of_login("intern").authorize("system")
    assert refused.value.status_code == 403


async def test_a_machine_reads_back_only_what_it_started(machine: _FakeClient) -> None:
    mine = await _key_caller()
    theirs = await _key_caller()

    started = {"started_by_id": mine.started_by_id}
    mine.assert_can_read(started)
    with pytest.raises(HTTPException) as refused:
        theirs.assert_can_read(started)
    assert refused.value.status_code == 404
