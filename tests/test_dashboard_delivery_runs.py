from agent.dashboard import thread_api
from agent.dashboard.delivery_runs import build_delivery_run_rollup


def test_delivery_rollup_reads_store_value_records() -> None:
    rollup = build_delivery_run_rollup(
        {},
        store_record={
            "value": {
                "queueStatus": "queued",
                "workerThreadId": "worker-thread",
                "artifactCount": 3,
                "gateRollup": "pending",
                "status": "running",
            }
        },
    )

    assert rollup == {
        "queueStatus": "queued",
        "workerThreadId": "worker-thread",
        "reviewerThreadId": None,
        "qaThreadId": None,
        "mergeWorkerThreadId": None,
        "pr": None,
        "branch": None,
        "previewUrl": None,
        "previewCount": 0,
        "artifactCount": 3,
        "gateRollup": {
            "status": "pending",
            "passed": 0,
            "failed": 0,
            "pending": 0,
            "total": 0,
        },
        "gates": [],
        "artifacts": [],
        "blockers": [],
        "blockerReason": None,
        "reviewedSha": None,
        "mergeStatus": None,
        "mergeResult": None,
    }


def test_thread_summary_includes_delivery_rollup_from_metadata() -> None:
    summary = thread_api._thread_summary(
        {
            "thread_id": "delivery-thread",
            "status": "idle",
            "metadata": {
                "title": "Ship dashboard slice",
                "delivery": {
                    "queue_status": "running",
                    "worker_thread_id": "worker-thread",
                    "reviewer_thread_id": "reviewer-thread",
                    "qa_thread_id": "qa-thread",
                    "merge_worker_thread_id": "merge-thread",
                    "preview_count": 2,
                    "artifact_count": 5,
                    "gate_rollup": {
                        "status": "blocked",
                        "passed": 3,
                        "failed": 1,
                        "pending": 2,
                    },
                    "blocker_reason": "QA gate failed",
                    "reviewed_sha": "abc123",
                    "merge_status": "blocked",
                },
                "pr_number": 42,
                "pr_url": "https://github.com/langchain-ai/open-swe/pull/42",
                "pr_state": "open",
                "pr_title": "Ship dashboard slice",
                "branch_name": "delivery/dashboard-slice",
                "base_branch": "main",
            },
        }
    )

    assert summary["delivery"] == {
        "queueStatus": "running",
        "workerThreadId": "worker-thread",
        "reviewerThreadId": "reviewer-thread",
        "qaThreadId": "qa-thread",
        "mergeWorkerThreadId": "merge-thread",
        "pr": {
            "number": 42,
            "title": "Ship dashboard slice",
            "state": "open",
            "headRef": "delivery/dashboard-slice",
            "baseRef": "main",
            "url": "https://github.com/langchain-ai/open-swe/pull/42",
        },
        "branch": "delivery/dashboard-slice",
        "previewUrl": None,
        "previewCount": 2,
        "artifactCount": 5,
        "gateRollup": {
            "status": "blocked",
            "passed": 3,
            "failed": 1,
            "pending": 2,
            "total": 6,
        },
        "gates": [],
        "artifacts": [],
        "blockers": [],
        "blockerReason": "QA gate failed",
        "reviewedSha": "abc123",
        "mergeStatus": "blocked",
        "mergeResult": None,
    }


def test_thread_summary_mixes_persisted_queue_record_into_delivery_rollup() -> None:
    summary = thread_api._thread_summary(
        {
            "thread_id": "delivery-thread",
            "status": "idle",
            "metadata": {
                "title": "Ship dashboard slice",
                "delivery_queue_item_id": "sports-cms:linear:ENG-123",
                "delivery": {
                    "queue_status": "running",
                    "worker_thread_id": "worker-thread",
                },
            },
        },
        delivery_store_record={
            "id": "sports-cms:linear:ENG-123",
            "status": "blocked",
            "reviewer_thread_id": "review-thread",
            "qa_thread_id": "qa-thread",
            "merge_worker_thread_id": "merge-thread",
            "branch": "delivery/sports-cms/eng-123",
            "qa_evidence": {
                "preview_url": "https://preview.example.test/eng-123",
                "gates": [{"name": "browser", "status": "passed"}],
                "screenshots": [{"url": "https://artifacts.example.test/screen.png"}],
                "blockers": [{"code": "qa_evidence_missing_video_or_trace"}],
            },
            "gate_rollup": {"status": "passed", "passed": 1, "failed": 0, "pending": 0, "total": 1},
            "blocker_reason": "qa_evidence_missing_video_or_trace",
            "merge_status": "blocked",
            "merge_audit": {"status": "blocked", "reason": "head_sha_mismatch"},
        },
    )

    assert summary["delivery"]["queueStatus"] == "blocked"
    assert summary["delivery"]["workerThreadId"] == "worker-thread"
    assert summary["delivery"]["reviewerThreadId"] == "review-thread"
    assert summary["delivery"]["qaThreadId"] == "qa-thread"
    assert summary["delivery"]["mergeWorkerThreadId"] == "merge-thread"
    assert summary["delivery"]["branch"] == "delivery/sports-cms/eng-123"
    assert summary["delivery"]["previewUrl"] == "https://preview.example.test/eng-123"
    assert summary["delivery"]["gates"] == [{"name": "browser", "status": "passed"}]
    assert summary["delivery"]["artifacts"] == [
        {"url": "https://artifacts.example.test/screen.png"}
    ]
    assert summary["delivery"]["blockers"] == [{"code": "qa_evidence_missing_video_or_trace"}]
    assert summary["delivery"]["mergeResult"] == {
        "status": "blocked",
        "reason": "head_sha_mismatch",
    }
