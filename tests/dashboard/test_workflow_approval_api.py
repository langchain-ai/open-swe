from agent.dashboard import workflow_approval_api


async def test_list_workflow_push_approvals_returns_records(monkeypatch) -> None:
    async def fake_thread_metadata(thread_id: str) -> dict:
        assert thread_id == "thread-1"
        return {"source": "dashboard"}

    async def fake_get_workflow_push_approvals(thread_id: str) -> dict:
        assert thread_id == "thread-1"
        return {
            "fp-1": {
                "fingerprint": "fp-1",
                "status": "pending",
                "repo": "langchain-ai/open-swe",
                "branch": "feature",
                "base_sha": "base",
                "head_sha": "head",
                "files": [".github/workflows/ci.yml"],
                "diff_stats": {"files": 1, "additions": 1, "deletions": 0},
                "diff_preview": "diff --git ...",
                "requested_at": "2026-07-07T00:00:00+00:00",
            }
        }

    monkeypatch.setattr(workflow_approval_api, "_thread_metadata", fake_thread_metadata)
    monkeypatch.setattr(
        workflow_approval_api,
        "get_workflow_push_approvals",
        fake_get_workflow_push_approvals,
    )

    response = await workflow_approval_api.list_workflow_push_approvals(
        "thread-1",
        session={"sub": "owner", "email": "owner@example.com"},
    )

    assert response["threadId"] == "thread-1"
    assert response["approvals"] == [
        {
            "fingerprint": "fp-1",
            "status": "pending",
            "repo": "langchain-ai/open-swe",
            "branch": "feature",
            "baseSha": "base",
            "headSha": "head",
            "files": [".github/workflows/ci.yml"],
            "diffStats": {"files": 1, "additions": 1, "deletions": 0},
            "diffPreview": "diff --git ...",
            "diffPreviewTruncated": False,
            "inheritedFrom": None,
            "approvalUrl": None,
            "requestedAt": "2026-07-07T00:00:00+00:00",
            "decidedAt": None,
            "decidedBy": None,
        }
    ]
