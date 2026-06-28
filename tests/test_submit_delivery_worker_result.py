from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent import delivery_queue as queue
from agent import delivery_review as review

tool = importlib.import_module("agent.tools.submit_delivery_worker_result")


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
        self.metadata.setdefault(thread_id, {}).update(metadata)


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()
        self.threads = _FakeThreads()


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(queue, "_client", lambda: client)
    monkeypatch.setattr(review, "_client", lambda: client)
    return client


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


def _worker_result() -> dict[str, Any]:
    gates = ["drupal_bootstrap", "browser_flow", "screenshot", "trace_or_video"]
    return {
        "cause": "The teaser component missed a mobile modifier.",
        "changed_files": ["web/themes/custom/sports/components/teaser/teaser.twig"],
        "before_proof": "Mobile fixture rendered without CTA state.",
        "after_proof": "Drupal preview renders the CTA state.",
        "executed_gates": [
            {"name": gate, "status": "passed", "source": "platform"} for gate in gates
        ],
        "risks": [],
        "pull_request_summary": "Fix mobile CTA state.",
        "preview_url": "https://preview.example.test/teaser",
        "screenshots": ["https://artifacts.example.test/teaser.png"],
        "traces": ["https://artifacts.example.test/trace.zip"],
        "pr": {
            "number": 42,
            "url": "https://github.com/example/sports-cms/pull/42",
            "head": {"sha": "head-sha"},
        },
    }


async def _running_item() -> dict[str, Any]:
    item = await queue.upsert_delivery_queue_item(
        {
            "project_id": "sports-cms",
            "provider": "linear",
            "external_work_item_id": "SPORT-1",
            "title": "Fix teaser",
            "description": "Fix Sports CMS teaser.",
            "worker_thread_id": "worker-thread",
            "gate_policy": {
                "qa_evidence": True,
                "blocking_gates": [
                    "drupal_bootstrap",
                    "browser_flow",
                    "screenshot",
                    "trace_or_video",
                ],
            },
        },
        preflight=_ready_preflight(),
    )
    return await queue.transition_delivery_queue_status(item["id"], "running")


@pytest.mark.asyncio
async def test_submit_delivery_worker_result_ingests_and_launches_review(
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = await _running_item()
    dispatch = AsyncMock(return_value={"run_id": "review-run"})
    monkeypatch.setattr(review, "dispatch_agent_run", dispatch)
    monkeypatch.setattr(
        tool,
        "get_config",
        lambda: {"configurable": {"delivery_queue_item_id": item["id"]}},
    )

    result = await tool.submit_delivery_worker_result(_worker_result())

    updated = await queue.read_delivery_queue_item(item["id"])
    assert result["success"] is True
    assert result["queue_status"] == "review"
    assert result["review_launch"]["status"] == "launched"
    assert updated["status"] == "review"
    assert updated["qa_evidence"]["complete"] is True
    assert updated["review_result"]["status"] == "pending"
    assert [call.kwargs["assistant_id"] for call in dispatch.await_args_list] == [
        "reviewer",
        "agent",
    ]


@pytest.mark.asyncio
async def test_submit_delivery_worker_result_blocks_invalid_evidence(
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = await _running_item()
    monkeypatch.setattr(
        tool,
        "get_config",
        lambda: {"configurable": {"delivery_queue_item_id": item["id"]}},
    )

    result = await tool.submit_delivery_worker_result({"cause": "too little"})

    updated = await queue.read_delivery_queue_item(item["id"])
    assert result["success"] is False
    assert result["queue_status"] == "blocked"
    assert result["review_launch"] is None
    assert updated["status"] == "blocked"
    assert updated["blockers"]


@pytest.mark.asyncio
async def test_submit_delivery_worker_result_requires_delivery_queue_context(
    fake_client: _FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool, "get_config", lambda: {"configurable": {}})

    result = await tool.submit_delivery_worker_result(_worker_result())

    assert result == {
        "success": False,
        "error": "delivery_queue_item_id is missing from run config",
    }
