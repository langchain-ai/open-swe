"""Trusted workflow and repository identity are checked before controller operations."""

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from unittest.mock import AsyncMock
from uuid import uuid7

import httpx2
import pytest

from openswe.github.http import GitHubClient, RepoClient
from openswe.github.queue import NativeQueueSnapshot
from openswe.github.repositories import Repository
from openswe.merge_queue.admission import CheckSource, QueuePolicy
from openswe.merge_queue.configuration import QueueConfiguration
from openswe.merge_queue.controller import QueueController
from openswe.merge_queue.store import MergeQueue
from openswe.merge_queue.validation_contract import ValidationPolicy


def native_response(
    numbers: tuple[int, ...] = (1, 2, 3), main: str = "a" * 40
) -> dict[str, object]:
    entries = []
    base = main
    for position, number in enumerate(numbers, 1):
        head = f"{number:040x}"
        candidate = hashlib.sha1(f"{base}:{head}".encode()).hexdigest()
        entries.append(
            {
                "id": f"entry-{number}",
                "position": position,
                "state": "AWAITING_CHECKS",
                "enqueuedAt": "2026-01-01T00:00:00Z",
                "baseCommit": {"oid": base},
                "headCommit": {
                    "oid": candidate,
                    "parents": {
                        "totalCount": 2,
                        "nodes": [{"oid": base}, {"oid": head}],
                    },
                },
                "pullRequest": {
                    "number": number,
                    "headRefOid": head,
                    "state": "OPEN",
                    "reviewDecision": "APPROVED",
                    "isDraft": False,
                    "baseRefName": "main",
                    "headRepository": {"nameWithOwner": "acme/widgets"},
                },
            }
        )
        base = candidate
    return {
        "databaseId": 42,
        "nameWithOwner": "acme/widgets",
        "ref": {"name": "main", "target": {"oid": main}},
        "mergeQueue": {
            "id": "queue-1",
            "entries": {
                "totalCount": len(entries),
                "nodes": entries,
                "pageInfo": {"hasNextPage": False},
            },
        },
    }


async def test_native_candidates_follow_commit_ancestry_through_recovery() -> None:
    response = native_response()

    def respond(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/graphql"
        assert json.loads(request.content)["variables"]["ref"] == "refs/heads/main"
        return httpx2.Response(200, json={"data": {"repository": response}})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        repo = GitHubClient(http).repo("acme", "widgets")
        initial = await repo.native_queue("main")
        assert [[p.number for p in c.members] for c in initial.candidates.values()] == [
            [1],
            [1, 2],
            [1, 2, 3],
        ]
        assert initial.queue is not None
        # Dequeue B while GitHub still exposes C's old ABC commit.
        stale = initial.model_copy(
            update={
                "queue": initial.queue.model_copy(
                    update={
                        "entries": initial.queue.entries.model_copy(
                            update={
                                "nodes": (
                                    initial.queue.entries.nodes[0],
                                    initial.queue.entries.nodes[2],
                                ),
                                "total_count": 2,
                            }
                        ),
                    }
                )
            }
        )
        assert len(stale.candidates) == 1
        response = native_response((1, 3, 4))
        rebuilt = await repo.native_queue("main")
        assert [[p.number for p in c.members] for c in rebuilt.candidates.values()] == [
            [1],
            [1, 3],
            [1, 3, 4],
        ]
        ac, acd = list(rebuilt.candidates)[1:]
        # D's candidate is unchanged when AC lands; membership is now relative to new main.
        response = native_response((4,), main=ac)
        after_merge = await repo.native_queue("main")
        assert list(after_merge.candidates) == [acd]
        assert [p.number for p in after_merge.candidates[acd].members] == [4]
        assert after_merge.candidates[acd].base_sha == ac


@pytest.mark.parametrize("fault", ["changed_head", "unbuilt_prefix", "overflow", "graphql_error"])
async def test_native_observation_never_infers_members_from_incomplete_state(fault: str) -> None:
    snapshot = NativeQueueSnapshot.model_validate(native_response())
    assert snapshot.queue is not None
    first, *rest = snapshot.queue.entries.nodes
    if fault == "changed_head":
        first = first.model_copy(
            update={"pull": first.pull.model_copy(update={"head_sha": "f" * 40})}
        )
    elif fault == "unbuilt_prefix":
        first = first.model_copy(update={"head_sha": None, "base_sha": None, "parents": ()})
    entries = snapshot.queue.entries.model_copy(
        update={"nodes": (first, *rest), "has_next": fault == "overflow"}
    )
    snapshot = snapshot.model_copy(
        update={"queue": snapshot.queue.model_copy(update={"entries": entries})}
    )

    def respond(request: httpx2.Request) -> httpx2.Response:
        body: dict[str, object] = {"data": {"repository": snapshot.model_dump(mode="json")}}
        if fault == "graphql_error":
            body["errors"] = [{"message": "Access denied"}]
        return httpx2.Response(200, json=body)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as http:
        repo = GitHubClient(http).repo("acme", "widgets")
        if fault in {"overflow", "graphql_error"}:
            with pytest.raises(ValueError):
                await repo.native_queue("main")
        else:
            assert (await repo.native_queue("main")).candidates == {}


@pytest.mark.parametrize(
    ("mode", "fault"),
    [
        ("custom", None),
        ("custom", "repository"),
        ("custom", "workflow"),
        ("native", None),
        ("native", "repository"),
    ],
)
async def test_controller_rejects_changed_trusted_inputs(
    monkeypatch: pytest.MonkeyPatch, mode: Literal["custom", "native"], fault: str | None
) -> None:
    monkeypatch.setenv("GITHUB_APP_ID", "99")
    if mode == "custom":
        monkeypatch.setenv("MERGE_QUEUE_DISPATCH_SECRET", "s" * 32)
    else:
        monkeypatch.delenv("MERGE_QUEUE_DISPATCH_SECRET", raising=False)
    repository = Repository(full_name="acme/widgets")
    queue = MergeQueue(
        workspace_id=uuid7(), repository_id=repository.id, target_branch="main", mode=mode
    )
    workflow = b"reviewed workflow"
    controller = QueueController(
        QueueConfiguration(
            mode=mode,
            workspace_id=queue.workspace_id,
            repository="acme/widgets",
            repository_id=42,
            dispatch_url="https://dispatcher.example/queue" if mode == "custom" else None,
            operator_emails=frozenset({"operator@example.com"}),
            workflow_digest=hashlib.sha256(workflow).hexdigest() if mode == "custom" else None,
            policy=QueuePolicy(
                checks={"checks": CheckSource(app_id=1, workflow_path=".github/workflows/ci.yml")},
                validation=ValidationPolicy(dispatch_actor_id=789),
            )
            if mode == "custom"
            else QueuePolicy(checks={}),
        )
    )
    monkeypatch.setattr(Repository, "get", AsyncMock(return_value=repository))

    def respond(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/repos/acme/widgets":
            return httpx2.Response(200, json={"id": 43 if fault == "repository" else 42})
        if mode == "custom" and path.endswith("/branches/main"):
            return httpx2.Response(200, json={"commit": {"sha": "a" * 40}})
        if mode == "custom" and path.endswith(
            "/contents/.github/workflows/merge_queue_validate.yml"
        ):
            assert request.url.params["ref"] == "a" * 40
            return httpx2.Response(200, content=b"changed" if fault == "workflow" else workflow)
        pytest.fail(f"Unexpected GitHub request: {path}")

    @asynccontextmanager
    async def as_app(
        owner: str, repo: str, *, permissions: dict[str, str]
    ) -> AsyncIterator[GitHubClient]:
        assert (owner, repo) == ("acme", "widgets")
        if mode == "native":
            assert permissions == {"contents": "read", "metadata": "read", "pull_requests": "read"}
        else:
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
