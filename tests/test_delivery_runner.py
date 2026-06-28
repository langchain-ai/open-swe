from __future__ import annotations

from typing import Any

import pytest

from agent import delivery_queue as queue
from agent import delivery_runner as runner
from agent import project_registry


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


class _FakeThreads:
    def __init__(self) -> None:
        self.metadata: dict[str, dict[str, Any]] = {}
        self.statuses: dict[str, str] = {}

    async def create(
        self,
        *,
        thread_id: str,
        metadata: dict[str, Any],
        if_exists: str = "raise",
    ) -> dict[str, Any]:
        self.metadata.setdefault(thread_id, dict(metadata))
        return {"thread_id": thread_id, "metadata": self.metadata[thread_id]}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        current = self.metadata.setdefault(thread_id, {})
        current.update(metadata)

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {
            "thread_id": thread_id,
            "status": self.statuses.get(thread_id, "idle"),
            "metadata": self.metadata.get(thread_id, {}),
        }


class _FakeRuns:
    def __init__(self) -> None:
        self.by_thread: dict[str, list[dict[str, Any]]] = {}

    async def list(
        self,
        thread_id: str,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        runs = self.by_thread.get(thread_id, [])
        if status is not None:
            runs = [run for run in runs if run.get("status") == status]
        return runs[:limit]


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()
        self.threads = _FakeThreads()
        self.runs = _FakeRuns()


class _DispatchRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        thread_id: str,
        content: str,
        configurable: dict[str, Any],
        *,
        source: str,
        assistant_id: str = "agent",
        metadata: dict[str, Any] | None = None,
        client: Any = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "thread_id": thread_id,
                "content": content,
                "configurable": configurable,
                "source": source,
                "assistant_id": assistant_id,
                "metadata": metadata,
                "client": client,
            }
        )
        return {"run_id": "run-worker-1"}


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(queue, "_client", lambda: client)
    monkeypatch.setattr(project_registry, "_client", lambda: client)
    return client


@pytest.fixture
def dispatch_recorder(monkeypatch: pytest.MonkeyPatch) -> _DispatchRecorder:
    recorder = _DispatchRecorder()
    monkeypatch.setattr(runner, "dispatch_agent_run", recorder)
    return recorder


def _ready_preflight() -> queue.PreflightInput:
    return {
        "active_project": True,
        "readiness": True,
        "issue_context": True,
        "credentials": True,
        "ai_hub_ready": True,
        "sandbox_profile": True,
        "budget": True,
        "duplicate_active_run": False,
        "kill_switch": False,
    }


async def _queued_item(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "project_id": "project-1",
        "provider": "linear",
        "external_work_item_id": "ENG-123",
        "title": "Fix checkout totals",
        "description": "Checkout totals drift after tax recalculation.",
        "branch": "delivery/checkout-totals",
        "base_branch": "main",
        "delivery_mode": "pull_request",
        "risk_class": "medium",
        "model_snapshot": "openai:gpt-5",
        "credential_identity": "github:user:octocat",
        "github_login": "octocat",
        "repo": {"owner": "langchain-ai", "name": "open-swe"},
        "sandbox_profile": {"provider": "langsmith", "profile": "sports-cms"},
        "worktree": {"path": "/tmp/open-swe/worktrees/delivery-checkout-totals"},
        "gates": [{"name": "unit", "status": "pending"}],
        "artifacts": [{"type": "preview", "url": "https://preview.example.test"}],
    }
    payload.update(overrides)
    return await queue.upsert_delivery_queue_item(payload, preflight=_ready_preflight())


async def test_launch_delivery_worker_dispatches_agent_run(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    record = await _queued_item()

    result = await runner.launch_delivery_worker(record["id"], client=fake_client)

    assert result["status"] == "launched"
    assert result["item_id"] == record["id"]
    assert result["worker_thread_id"].startswith("delivery-worker-")
    assert result["run_id"] == "run-worker-1"
    assert len(dispatch_recorder.calls) == 1
    call = dispatch_recorder.calls[0]
    assert call["thread_id"] == result["worker_thread_id"]
    assert call["assistant_id"] == "agent"
    assert call["source"] == "delivery_queue"
    assert call["client"] is fake_client
    assert call["configurable"]["delivery_queue_item_id"] == record["id"]
    assert call["configurable"]["repo"] == {"owner": "langchain-ai", "name": "open-swe"}


async def test_launch_delivery_worker_refuses_duplicate_active_run(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    record = await _queued_item(worker_thread_id="worker-existing")
    fake_client.runs.by_thread["worker-existing"] = [{"run_id": "run-active", "status": "running"}]

    result = await runner.launch_delivery_worker(record["id"], client=fake_client)

    assert result["status"] == "refused"
    assert result["reason"] == "duplicate_active_run"
    assert result["item_id"] == record["id"]
    assert result["worker_thread_id"] == "worker-existing"
    assert result["active_run_id"] == "run-active"
    assert result["active_run_status"] == "running"
    assert result["blockers"] == [
        {
            "code": "duplicate_active_run",
            "message": "Another active run already exists for this work item.",
        }
    ]
    assert dispatch_recorder.calls == []
    blocked = await queue.read_delivery_queue_item(record["id"])
    assert blocked["status"] == "blocked"
    assert blocked["status_reason"] == "start_preflight_failed"
    assert blocked["blockers"] == [
        {
            "code": "duplicate_active_run",
            "message": "Another active run already exists for this work item.",
        }
    ]


async def test_launch_delivery_worker_refuses_not_queued_item(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    record = await _queued_item(status="blocked")

    result = await runner.launch_delivery_worker(record["id"], client=fake_client)

    assert result == {
        "status": "refused",
        "reason": "not_queued",
        "item_id": record["id"],
        "current_status": "blocked",
    }
    assert dispatch_recorder.calls == []


async def test_launch_delivery_worker_blocks_auto_mode_limit_before_dispatch(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    record = await _queued_item()

    result = await runner.launch_delivery_worker(
        record["id"],
        client=fake_client,
        auto_mode={
            "ready": False,
            "blockers": [
                {
                    "code": "daily_budget",
                    "message": "Daily Auto-Mode budget is exhausted.",
                }
            ],
        },
    )

    blocked = await queue.read_delivery_queue_item(record["id"])
    assert result["status"] == "refused"
    assert result["reason"] == "daily_budget"
    assert dispatch_recorder.calls == []
    assert blocked["status"] == "blocked"
    assert blocked["blockers"] == [
        {"code": "daily_budget", "message": "Daily Auto-Mode budget is exhausted."}
    ]


async def test_launch_delivery_worker_writes_required_thread_metadata(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    record = await _queued_item()

    result = await runner.launch_delivery_worker(record["id"], client=fake_client)

    metadata = fake_client.threads.metadata[result["worker_thread_id"]]
    assert metadata["delivery_queue_item_id"] == record["id"]
    assert metadata["source_provider"] == "linear"
    assert metadata["source_id"] == "ENG-123"
    assert metadata["worker_thread_id"] == result["worker_thread_id"]
    assert metadata["delivery_worker_thread_id"] == result["worker_thread_id"]
    assert metadata["branch"] == "delivery/checkout-totals"
    assert metadata["branch_name"] == "delivery/checkout-totals"
    assert metadata["delivery_mode"] == "pull_request"
    assert metadata["risk_class"] == "medium"
    assert metadata["model_snapshot"] == "openai:gpt-5"
    assert metadata["credential_identity"] == "github:user:octocat"
    assert metadata["repo_owner"] == "langchain-ai"
    assert metadata["repo_name"] == "open-swe"
    assert metadata["delivery"] == {
        "queue_status": "running",
        "worker_thread_id": result["worker_thread_id"],
        "queue_item_id": record["id"],
    }
    assert len(dispatch_recorder.calls) == 1


async def test_delivery_worker_prompt_contains_worker_contract(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    record = await _queued_item()

    await runner.launch_delivery_worker(record["id"], client=fake_client)

    prompt = dispatch_recorder.calls[0]["content"].lower()
    assert record["title"].lower() in prompt
    assert "reproduce the failure in the smallest representative environment" in prompt
    assert "prove the root cause" in prompt
    assert "make the smallest credible fix" in prompt
    assert "relevant regression tests" in prompt
    assert "do not fold unrelated refactors into the patch" in prompt
    assert "cause" in prompt
    assert "files" in prompt
    assert "proof" in prompt
    assert "risks" in prompt
    assert "pull-request summary" in prompt


async def test_launch_delivery_worker_transitions_queue_status_to_running(
    fake_client: _FakeClient,
    dispatch_recorder: _DispatchRecorder,
) -> None:
    record = await _queued_item()

    result = await runner.launch_delivery_worker(record["id"], client=fake_client)

    updated = await queue.read_delivery_queue_item(record["id"])
    assert updated["status"] == "running"
    assert updated["previous_status"] == "queued"
    assert updated["status_reason"] == "worker launch dispatched"
    assert updated["worker_thread_id"] == result["worker_thread_id"]
    assert updated["latest_run_id"] == "run-worker-1"
    assert updated["latest_run"] == {
        "run_id": "run-worker-1",
        "status": "running",
        "worker_thread_id": result["worker_thread_id"],
        "branch": "delivery/checkout-totals",
        "base_branch": "main",
        "sandbox_profile": {"provider": "langsmith", "profile": "sports-cms"},
        "worktree": {"path": "/tmp/open-swe/worktrees/delivery-checkout-totals"},
        "model_snapshot": "openai:gpt-5",
        "credential_identity": "github:user:octocat",
        "risk_class": "medium",
        "gates": [{"name": "unit", "status": "pending"}],
        "artifacts": [{"type": "preview", "url": "https://preview.example.test"}],
        "blocker_reason": None,
    }
    assert updated["runs"] == [updated["latest_run"]]
    assert len(dispatch_recorder.calls) == 1
