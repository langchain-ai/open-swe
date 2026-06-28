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
        "previewCount": 0,
        "artifactCount": 3,
        "gateRollup": {
            "status": "pending",
            "passed": 0,
            "failed": 0,
            "pending": 0,
            "total": 0,
        },
        "blockerReason": None,
        "reviewedSha": None,
        "mergeStatus": None,
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
        "previewCount": 2,
        "artifactCount": 5,
        "gateRollup": {
            "status": "blocked",
            "passed": 3,
            "failed": 1,
            "pending": 2,
            "total": 6,
        },
        "blockerReason": "QA gate failed",
        "reviewedSha": "abc123",
        "mergeStatus": "blocked",
    }
