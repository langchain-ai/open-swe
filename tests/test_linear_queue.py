from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet

from agent import delivery_queue as queue
from agent import linear_queue, project_registry
from agent.dashboard import provider_pat_vault
from agent.linear_queue import (
    LinearQueueEligibilityPolicy,
    LinearQueueFieldMappings,
    poll_configured_linear_delivery_queues,
    poll_linear_delivery_queue,
    preview_linear_delivery_queue,
)


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: list[str], key: str) -> dict[str, Any] | None:
        value = self.items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(self, namespace: list[str], key: str, value: dict[str, Any]) -> None:
        self.items[(tuple(namespace), key)] = value

    async def search_items(
        self,
        namespace: list[str],
        filter: dict[str, Any] | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> dict[str, Any]:
        values = [
            value
            for (stored_namespace, _), value in self.items.items()
            if stored_namespace == tuple(namespace)
        ]
        if filter:
            values = [
                value
                for value in values
                if all(value.get(key) == expected for key, expected in filter.items())
            ]
        return {"items": [{"value": value} for value in values[offset : offset + limit]]}


class _FakeQueueClient:
    def __init__(self) -> None:
        self.store = _FakeStore()


class _FakeLinearClient:
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        self.issues = issues
        self.list_kwargs: dict[str, Any] | None = None
        self.write_calls: list[str] = []

    async def list_issues(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.list_kwargs = kwargs
        return self.issues

    async def update_issue(self, *_: Any, **__: Any) -> None:
        self.write_calls.append("update_issue")

    async def comment_on_issue(self, *_: Any, **__: Any) -> None:
        self.write_calls.append("comment_on_issue")

    async def transition_issue(self, *_: Any, **__: Any) -> None:
        self.write_calls.append("transition_issue")


@pytest.fixture
def fake_queue_client(monkeypatch: pytest.MonkeyPatch) -> _FakeQueueClient:
    client = _FakeQueueClient()
    monkeypatch.setattr(queue, "_client", lambda: client)
    monkeypatch.setattr(project_registry, "_client", lambda: client)
    monkeypatch.setattr(provider_pat_vault, "_client", lambda: client)
    return client


def _issue(**overrides: Any) -> dict[str, Any]:
    issue = {
        "id": "lin-1",
        "identifier": "ENG-123",
        "title": "Add delivery queue polling",
        "description": "Poll ready work items.",
        "url": "https://linear.app/acme/issue/ENG-123",
        "priority": 2,
        "priorityLabel": "High",
        "state": {"id": "state-1", "name": "Ready", "type": "started"},
        "team": {"id": "team-1", "key": "ENG", "name": "Engineering"},
        "project": {"id": "project-linear-1", "name": "Delivery"},
        "labels": {"nodes": [{"id": "label-1", "name": "agent-ready"}]},
    }
    return {**issue, **overrides}


async def test_ready_linear_issue_is_queued(fake_queue_client: _FakeQueueClient) -> None:
    result = await poll_linear_delivery_queue(
        LinearQueueEligibilityPolicy(project_id="delivery-project"),
        client=_FakeLinearClient([_issue()]),
    )

    records = await queue.list_delivery_queue_items()
    assert result["items"] == 1
    assert len(records) == 1
    assert records[0]["id"] == "delivery-project:linear:lin-1"
    assert records[0]["status"] == "queued"
    assert records[0]["title"] == "Add delivery queue polling"
    assert records[0]["url"] == "https://linear.app/acme/issue/ENG-123"
    assert records[0]["linear"]["identifier"] == "ENG-123"
    assert records[0]["priority"] == 2
    assert len(fake_queue_client.store.items) == 1


async def test_missing_readiness_label_can_skip_or_mark_not_ready(
    fake_queue_client: _FakeQueueClient,
) -> None:
    missing_label_issue = _issue(labels={"nodes": []})
    skipped = await poll_linear_delivery_queue(
        LinearQueueEligibilityPolicy(project_id="delivery-project"),
        client=_FakeLinearClient([missing_label_issue]),
    )

    marked = await poll_linear_delivery_queue(
        LinearQueueEligibilityPolicy(
            project_id="delivery-project",
            missing_readiness="not-ready",
        ),
        client=_FakeLinearClient([missing_label_issue]),
    )

    records = await queue.list_delivery_queue_items()
    assert skipped == {"status": "polled", "provider": "linear", "items": 0, "skipped": 1}
    assert marked["items"] == 1
    assert len(records) == 1
    assert records[0]["status"] == "not-ready"
    assert records[0]["blockers"] == [
        {"code": "readiness", "message": "Work item is not ready for delivery."}
    ]


async def test_readiness_removed_updates_existing_queue_item(
    fake_queue_client: _FakeQueueClient,
) -> None:
    policy = LinearQueueEligibilityPolicy(
        project_id="delivery-project",
        missing_readiness="not-ready",
    )
    await poll_linear_delivery_queue(policy, client=_FakeLinearClient([_issue()]))

    result = await poll_linear_delivery_queue(
        policy,
        client=_FakeLinearClient([_issue(labels={"nodes": []})]),
    )

    records = await queue.list_delivery_queue_items()
    assert result["items"] == 1
    assert len(records) == 1
    assert records[0]["id"] == "delivery-project:linear:lin-1"
    assert records[0]["status"] == "paused"
    assert records[0]["blockers"] == [
        {"code": "readiness", "message": "Work item is not ready for delivery."}
    ]
    assert len(fake_queue_client.store.items) == 1


async def test_missing_required_context_creates_blocked_queue_item(
    fake_queue_client: _FakeQueueClient,
) -> None:
    await poll_linear_delivery_queue(
        LinearQueueEligibilityPolicy(
            project_id="delivery-project",
            required_fields=("description",),
        ),
        client=_FakeLinearClient([_issue(description="")]),
    )

    records = await queue.list_delivery_queue_items()
    assert len(records) == 1
    assert records[0]["status"] == "blocked"
    assert records[0]["missing_required_fields"] == ["description"]
    assert records[0]["blockers"] == [
        {"code": "issue_context", "message": "Issue context is missing."}
    ]


async def test_poller_has_no_linear_write_side_effects(
    fake_queue_client: _FakeQueueClient,
) -> None:
    linear_client = _FakeLinearClient([_issue()])

    await poll_linear_delivery_queue(
        LinearQueueEligibilityPolicy(project_id="delivery-project"),
        client=linear_client,
    )

    assert linear_client.write_calls == []


async def test_preview_maps_issues_without_queue_or_linear_write_side_effects(
    fake_queue_client: _FakeQueueClient,
) -> None:
    linear_client = _FakeLinearClient(
        [
            _issue(id="lin-ready"),
            _issue(id="lin-not-ready", labels={"nodes": []}),
            _issue(id="lin-blocked", description=""),
            _issue(id="lin-ignored", state={"id": "state-2", "name": "Done", "type": "completed"}),
        ]
    )

    result = await preview_linear_delivery_queue(
        LinearQueueEligibilityPolicy(
            project_id="delivery-project",
            missing_readiness="not-ready",
            excluded_statuses=("done", "completed"),
            required_fields=("description",),
        ),
        client=linear_client,
    )

    records = await queue.list_delivery_queue_items()
    assert result["counts"] == {"queued": 1, "not-ready": 1, "blocked": 1, "ignored": 1}
    assert [item["action"] for item in result["items"]] == [
        "queued",
        "not-ready",
        "blocked",
        "ignored",
    ]
    assert result["items"][2]["missing_required_fields"] == ["description"]
    assert records == []
    assert linear_client.write_calls == []


async def test_dedupe_key_uses_project_provider_and_external_id(
    fake_queue_client: _FakeQueueClient,
) -> None:
    await poll_linear_delivery_queue(
        LinearQueueEligibilityPolicy(project_id="delivery-project-a"),
        client=_FakeLinearClient([_issue()]),
    )
    await poll_linear_delivery_queue(
        LinearQueueEligibilityPolicy(project_id="delivery-project-b"),
        client=_FakeLinearClient([_issue()]),
    )

    records = await queue.list_delivery_queue_items()
    assert {record["id"] for record in records} == {
        "delivery-project-a:linear:lin-1",
        "delivery-project-b:linear:lin-1",
    }
    assert len(records) == 2
    assert len(fake_queue_client.store.items) == 2


async def test_policy_scope_excluded_statuses_and_field_mappings(
    fake_queue_client: _FakeQueueClient,
) -> None:
    linear_client = _FakeLinearClient(
        [
            _issue(id="lin-team", team={"id": "team-2", "key": "OPS", "name": "Operations"}),
            _issue(id="lin-project", project={"id": "project-linear-2", "name": "Other"}),
            _issue(id="lin-done", state={"id": "state-2", "name": "Done", "type": "completed"}),
            _issue(
                id="lin-keep",
                custom={"risk": "high", "priority": 1, "priority_label": "Urgent"},
            ),
        ]
    )

    await poll_linear_delivery_queue(
        LinearQueueEligibilityPolicy(
            project_id="delivery-project",
            team_keys=("ENG",),
            linear_project_ids=("project-linear-1",),
            excluded_statuses=("done", "completed"),
            fields=LinearQueueFieldMappings(
                risk="custom.risk",
                priority="custom.priority",
                priority_label="custom.priority_label",
            ),
        ),
        client=linear_client,
    )

    records = await queue.list_delivery_queue_items()
    assert len(records) == 1
    assert records[0]["external_work_item_id"] == "lin-keep"
    assert records[0]["risk"] == "high"
    assert records[0]["priority"] == 1
    assert records[0]["priority_label"] == "Urgent"
    assert linear_client.list_kwargs == {
        "readiness_label": "agent-ready",
        "team_ids": (),
        "team_keys": ("ENG",),
        "team_names": (),
        "linear_project_ids": ("project-linear-1",),
        "linear_project_names": (),
        "excluded_statuses": ("done", "completed"),
    }


async def test_configured_linear_project_policy_is_polled(
    fake_queue_client: _FakeQueueClient,
) -> None:
    await project_registry.upsert_delivery_project(
        {
            "project_id": "sports-cms",
            "name": "Sports CMS",
            "tracker": {
                "provider": "linear",
                "config": {
                    "team_keys": ["ENG"],
                    "linear_project_ids": ["project-linear-1"],
                },
            },
            "vcs": {"provider": "github", "config": {"owner": "example", "repo": "sports-cms"}},
            "queue_eligibility_policy": {
                "labels": ["agent-ready"],
                "missing_readiness": "not-ready",
                "excluded_statuses": ["done", "completed"],
                "required_fields": ["description"],
            },
        }
    )

    result = await poll_configured_linear_delivery_queues(client=_FakeLinearClient([_issue()]))

    records = await queue.list_delivery_queue_items()
    assert result == {
        "status": "polled",
        "provider": "linear",
        "projects": 1,
        "items": 1,
        "skipped": 0,
        "errors": [],
    }
    assert records[0]["id"] == "sports-cms:linear:lin-1"
    assert records[0]["status"] == "queued"


async def test_configured_linear_poll_uses_project_member_provider_pat(
    fake_queue_client: _FakeQueueClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    await provider_pat_vault.upsert_provider_pat(
        "octocat",
        provider="linear",
        token="lin_pat_project_member_1234",
    )
    await project_registry.upsert_delivery_project(
        {
            "project_id": "sports-cms",
            "name": "Sports CMS",
            "tracker": {
                "provider": "linear",
                "config": {"team_keys": ["ENG"], "linear_project_ids": ["project-linear-1"]},
            },
            "vcs": {"provider": "github", "config": {"owner": "example", "repo": "sports-cms"}},
            "queue_eligibility_policy": {"labels": ["agent-ready"]},
            "membership": {"users": ["octocat"]},
        }
    )
    captured: dict[str, Any] = {}

    async def fake_graphql_request(
        _query: str,
        _variables: dict[str, Any] | None = None,
        *,
        token: str | None = None,
    ) -> dict[str, Any]:
        captured["token"] = token
        return {
            "issues": {
                "nodes": [_issue()],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }

    monkeypatch.setattr(linear_queue, "_graphql_request", fake_graphql_request)

    result = await poll_configured_linear_delivery_queues()

    records = await queue.list_delivery_queue_items()
    audit = await provider_pat_vault.list_provider_pat_audit("octocat")
    assert result["status"] == "polled"
    assert result["items"] == 1
    assert captured["token"] == "lin_pat_project_member_1234"
    assert records[0]["id"] == "sports-cms:linear:lin-1"
    assert audit[0]["provider"] == "linear"
    assert audit[0]["action"] == "queue_poll"
