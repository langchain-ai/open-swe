"""Trusted workflow and repository identity are checked before controller operations."""

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid7

import httpx2
import pytest

from openswe.github.http import GitHubClient, RepoClient
from openswe.github.repositories import Repository
from openswe.merge_queue.admission import CheckSource, QueuePolicy
from openswe.merge_queue.configuration import QueueConfiguration
from openswe.merge_queue.controller import QueueController
from openswe.merge_queue.store import MergeQueue
from openswe.merge_queue.validation_contract import ValidationPolicy


@pytest.mark.parametrize("fault", [None, "repository", "workflow"])
async def test_controller_rejects_changed_trusted_inputs(
    monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "99")
    monkeypatch.setenv("MERGE_QUEUE_DISPATCH_SECRET", "s" * 32)
    monkeypatch.setenv("SANDBOX_TYPE", "langsmith")
    repository = Repository(full_name="acme/widgets")
    queue = MergeQueue(workspace_id=uuid7(), repository_id=repository.id, target_branch="main")
    workflow = b"reviewed workflow"
    controller = QueueController(
        QueueConfiguration(
            workspace_id=queue.workspace_id,
            repository="acme/widgets",
            repository_id=42,
            dispatch_url="https://dispatcher.example/queue",
            workflow_digest=hashlib.sha256(workflow).hexdigest(),
            policy=QueuePolicy(
                checks={"checks": CheckSource(app_id=1, workflow_path=".github/workflows/ci.yml")},
                validation=ValidationPolicy(dispatch_actor_id=789),
            ),
        )
    )
    monkeypatch.setattr(Repository, "get", AsyncMock(return_value=repository))

    def respond(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/repos/acme/widgets":
            return httpx2.Response(200, json={"id": 43 if fault == "repository" else 42})
        if path.endswith("/branches/main"):
            return httpx2.Response(200, json={"commit": {"sha": "a" * 40}})
        if path.endswith("/contents/.github/workflows/merge_queue_validate.yml"):
            assert request.url.params["ref"] == "a" * 40
            return httpx2.Response(200, content=b"changed" if fault == "workflow" else workflow)
        pytest.fail(f"Unexpected GitHub request: {path}")

    @asynccontextmanager
    async def as_app(
        owner: str, repo: str, *, permissions: dict[str, str]
    ) -> AsyncIterator[GitHubClient]:
        assert (owner, repo) == ("acme", "widgets")
        assert permissions["actions"] == "read"
        assert permissions["contents"] == "write"
        assert "variables" not in permissions
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
            yield GitHubClient(http)

    monkeypatch.setattr(GitHubClient, "as_app", as_app)
    if fault:
        with pytest.raises(ValueError):
            async with controller.open_repo(queue):
                pytest.fail("Untrusted inputs reached the controller")
    else:
        async with controller.open_repo(queue) as repo:
            assert isinstance(repo, RepoClient) and repo.full_name == "acme/widgets"
