from __future__ import annotations

from typing import Any

import pytest

from agent import delivery_queue as queue
from agent import delivery_results as results


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


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(queue, "_client", lambda: client)
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


async def _running_item(**overrides: Any) -> dict[str, Any]:
    record = await queue.upsert_delivery_queue_item(
        {
            "project_id": "sports-cms",
            "provider": "linear",
            "external_work_item_id": "ENG-123",
            "title": "Fix teaser card",
            "status": "running",
            "worker_thread_id": "worker-1",
            "latest_run": {
                "run_id": "run-1",
                "status": "running",
                "worker_thread_id": "worker-1",
            },
            "runs": [
                {
                    "run_id": "run-1",
                    "status": "running",
                    "worker_thread_id": "worker-1",
                }
            ],
            "gate_policy": {
                "blocking_gates": ["unit", "browser"],
                "advisory_gates": ["phpstan"],
            },
        },
        preflight=_ready_preflight(),
    )
    if overrides:
        record = await queue.transition_delivery_queue_status(
            record["id"],
            record["status"],
            extra=overrides,
        )
    return record


def _valid_result(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "cause": "The SDC variant missed the teaser modifier.",
        "changed_files": ["web/themes/custom/sports/components/teaser/teaser.twig"],
        "before_proof": ["Component render test fails before the patch."],
        "after_proof": ["Component render test passes after the patch."],
        "executed_gates": [
            {"name": "unit", "status": "passed", "platform_verified": True},
            {"name": "browser", "status": "passed", "platform_verified": True},
            {"name": "phpstan", "status": "failed", "platform_verified": True},
        ],
        "blockers": [],
        "risks": ["Theme cache rebuild required."],
        "pull_request_summary": "Fix teaser modifier rendering.",
        "preview_url": "https://preview.example.test/teaser",
        "screenshots": [{"url": "https://artifacts.example.test/teaser.png"}],
        "traces": [{"url": "https://artifacts.example.test/trace.zip"}],
        "pr": {"number": 42, "url": "https://github.com/example/sports-cms/pull/42"},
    }
    result.update(overrides)
    return result


async def test_ingest_valid_worker_result_moves_item_to_review(fake_client: _FakeClient) -> None:
    item = await _running_item()

    updated = await results.ingest_delivery_worker_result(item["id"], _valid_result())

    assert updated["status"] == "review"
    assert updated["status_reason"] == "worker result accepted"
    assert updated["qa_evidence"]["complete"] is True
    assert updated["qa_evidence"]["preview_url"] == "https://preview.example.test/teaser"
    assert updated["gate_rollup"] == {
        "status": "failed",
        "passed": 2,
        "failed": 1,
        "pending": 0,
        "total": 3,
    }
    assert updated["blockers"] == []
    assert updated["pr_number"] == 42
    assert updated["preview_count"] == 1
    assert updated["artifact_count"] == 2
    assert updated["latest_run"]["status"] == "review"
    assert updated["latest_run"]["worker_result"]["cause"].startswith("The SDC variant")
    assert updated["runs"][0]["qa_evidence"]["complete"] is True


async def test_ingest_blocks_missing_preview_for_browser_relevant_result(
    fake_client: _FakeClient,
) -> None:
    item = await _running_item()

    updated = await results.ingest_delivery_worker_result(
        item["id"],
        _valid_result(preview_url=""),
    )

    assert updated["status"] == "blocked"
    assert updated["status_reason"] == "qa_evidence_missing_preview_url"
    assert updated["qa_evidence"]["complete"] is False
    assert updated["blocker_reason"] == "qa_evidence_missing_preview_url"


async def test_ingest_blocks_missing_screenshot_for_browser_relevant_result(
    fake_client: _FakeClient,
) -> None:
    item = await _running_item()

    updated = await results.ingest_delivery_worker_result(
        item["id"],
        _valid_result(screenshots=[]),
    )

    assert updated["status"] == "blocked"
    assert updated["status_reason"] == "qa_evidence_missing_screenshot"
    assert updated["qa_evidence"]["complete"] is False


async def test_ingest_blocks_failed_blocking_gate(fake_client: _FakeClient) -> None:
    item = await _running_item()

    updated = await results.ingest_delivery_worker_result(
        item["id"],
        _valid_result(
            executed_gates=[
                {"name": "unit", "status": "failed", "platform_verified": True},
                {"name": "browser", "status": "passed", "platform_verified": True},
            ],
        ),
    )

    assert updated["status"] == "blocked"
    assert updated["status_reason"] == "blocking_gate_failed:unit"
    assert updated["qa_evidence"]["blockers"] == [
        {"code": "blocking_gate_failed:unit", "message": "Blocking gate unit failed."}
    ]


async def test_ingest_allows_failed_advisory_gate(fake_client: _FakeClient) -> None:
    item = await _running_item()

    updated = await results.ingest_delivery_worker_result(item["id"], _valid_result())

    assert updated["status"] == "review"
    assert updated["qa_evidence"]["complete"] is True
    assert updated["gate_rollup"]["failed"] == 1


async def test_ingest_adds_required_pr_qa_evidence_gate(fake_client: _FakeClient) -> None:
    item = await _running_item(
        gate_policy={
            "blocking_gates": ["unit", "browser", "pr_qa_evidence"],
            "advisory_gates": ["phpstan"],
        }
    )

    updated = await results.ingest_delivery_worker_result(item["id"], _valid_result())

    assert updated["status"] == "review"
    assert updated["qa_evidence"]["complete"] is True
    assert updated["qa_evidence"]["gate_rollup"] == {
        "status": "failed",
        "passed": 3,
        "failed": 1,
        "pending": 0,
        "total": 4,
    }
    pr_gate = next(
        gate for gate in updated["qa_evidence"]["gates"] if gate["name"] == "pr_qa_evidence"
    )
    assert pr_gate["status"] == "passed"
    assert pr_gate["source"] == "platform"
    assert "## QA Evidence" in pr_gate["body"]
    assert "https://preview.example.test/teaser" in pr_gate["body"]
    assert "https://artifacts.example.test/teaser.png" in pr_gate["body"]
    assert "https://artifacts.example.test/trace.zip" in pr_gate["body"]
    assert updated["worker_result"]["pull_request_evidence"]["body"] == pr_gate["body"]


async def test_ingest_blocks_required_pr_qa_evidence_without_pr(
    fake_client: _FakeClient,
) -> None:
    item = await _running_item(
        gate_policy={
            "blocking_gates": ["unit", "browser", "pr_qa_evidence"],
            "advisory_gates": ["phpstan"],
        }
    )

    updated = await results.ingest_delivery_worker_result(
        item["id"],
        _valid_result(pr={}),
    )

    assert updated["status"] == "blocked"
    assert updated["status_reason"] == "blocking_gate_failed:pr_qa_evidence"
    pr_gate = next(
        gate for gate in updated["qa_evidence"]["gates"] if gate["name"] == "pr_qa_evidence"
    )
    assert pr_gate["status"] == "failed"
    assert "Missing PR QA evidence: pull request." == pr_gate["output"]


async def test_ingest_blocks_unverified_blocking_gate(fake_client: _FakeClient) -> None:
    item = await _running_item()

    updated = await results.ingest_delivery_worker_result(
        item["id"],
        _valid_result(
            executed_gates=[
                {"name": "unit", "status": "passed", "platform_verified": False},
                {"name": "browser", "status": "passed", "platform_verified": True},
            ],
        ),
    )

    assert updated["status"] == "blocked"
    assert updated["status_reason"] == "blocking_gate_unverified:unit"
    assert updated["qa_evidence"]["complete"] is False


async def test_ingest_collects_platform_drupal_runtime_evidence(
    fake_client: _FakeClient,
    tmp_path,
) -> None:  # noqa: ANN001
    screenshot = tmp_path / "home.png"
    trace = tmp_path / "trace.zip"
    item = await _running_item(
        sandbox_profile={
            "provider": "ddev",
            "runtime": {
                "provider": "ddev",
                "project_path": str(tmp_path),
                "preview_url": "https://sports-preview.test/",
                "gates": [
                    {"name": "drupal_bootstrap", "command": "drush status"},
                    {
                        "name": "browser_flow",
                        "command": "curl -k -sS -o /dev/null -w '%{http_code}' {preview_url}",
                    },
                    {
                        "name": "screenshot",
                        "command": "chrome --screenshot={artifact_path} {preview_url}",
                        "artifact_type": "screenshot",
                        "artifact_path": str(screenshot),
                    },
                    {
                        "name": "trace_or_video",
                        "command": "trace --output={artifact_path} {preview_url}",
                        "artifact_type": "trace",
                        "artifact_path": str(trace),
                    },
                ],
            },
        },
        gate_policy={
            "blocking_gates": [
                "drupal_bootstrap",
                "browser_flow",
                "screenshot",
                "trace_or_video",
            ],
        },
    )
    commands: list[str] = []

    async def fake_runner(command: str, cwd: str, timeout: int) -> dict[str, Any]:
        commands.append(command)
        assert cwd == str(tmp_path)
        assert timeout == 120
        if str(screenshot) in command:
            screenshot.write_bytes(b"png")
        if str(trace) in command:
            trace.write_bytes(b"trace")
        return {"exit_code": 0, "output": "ok"}

    updated = await results.ingest_delivery_worker_result(
        item["id"],
        _valid_result(executed_gates=[], preview_url="", screenshots=[], traces=[]),
        platform_runner=fake_runner,
    )

    assert updated["status"] == "review"
    assert updated["qa_evidence"]["complete"] is True
    assert updated["qa_evidence"]["preview_url"] == "https://sports-preview.test/"
    assert updated["qa_evidence"]["screenshots"] == [str(screenshot)]
    assert updated["qa_evidence"]["traces"] == [str(trace)]
    assert [gate["source"] for gate in updated["qa_evidence"]["gates"]] == [
        "platform",
        "platform",
        "platform",
        "platform",
    ]
    assert updated["worker_result"]["platform_evidence"]["preview_url"] == (
        "https://sports-preview.test/"
    )
    assert len(commands) == 4


async def test_ingest_blocks_missing_platform_artifact(
    fake_client: _FakeClient,
    tmp_path,
) -> None:  # noqa: ANN001
    screenshot = tmp_path / "missing.png"
    item = await _running_item(
        sandbox_profile={
            "provider": "ddev",
            "runtime": {
                "provider": "ddev",
                "project_path": str(tmp_path),
                "preview_url": "https://sports-preview.test/",
                "gates": [
                    {
                        "name": "screenshot",
                        "command": "chrome --screenshot={artifact_path} {preview_url}",
                        "artifact_type": "screenshot",
                        "artifact_path": str(screenshot),
                    }
                ],
            },
        },
        gate_policy={"blocking_gates": ["screenshot"]},
    )

    async def fake_runner(_command: str, _cwd: str, _timeout: int) -> dict[str, Any]:
        return {"exit_code": 0, "output": "claimed success"}

    updated = await results.ingest_delivery_worker_result(
        item["id"],
        _valid_result(executed_gates=[], preview_url="", screenshots=[], traces=[]),
        platform_runner=fake_runner,
    )

    assert updated["status"] == "blocked"
    assert updated["status_reason"] == "blocking_gate_failed:screenshot"
    assert updated["qa_evidence"]["complete"] is False
    assert updated["qa_evidence"]["screenshots"] == []
