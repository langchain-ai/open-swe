from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx2
import pytest

from agent.review import approval_settings as settings
from agent.review.approval import (
    ApprovalDecision,
    ApprovalEvaluation,
    ApprovalEvidence,
    CriterionEvidence,
    PolicyRules,
)
from agent.review.approval_github import evaluate_pr_approval, fetch_approval_policy
from tests.conftest import FakeStore

HEAD = "a" * 40
BASE = "b" * 40
POLICY = settings.PolicyDefinition(
    rules=PolicyRules(required_checks=["tests"], human_review_paths=["auth/*"]),
    criteria_markdown="## Scope\nOnly documentation changes.",
)


GithubFixture = tuple[dict[str, object], list[httpx2.Request]]


@pytest.fixture
async def github(fake_store: FakeStore) -> AsyncIterator[GithubFixture]:
    await settings.CURRENT.put(
        "default", settings.PolicyRevision(repository=None, policy=POLICY, updated_by="admin")
    )
    payloads: dict[str, object] = {
        "/repos/org/repo/pulls/7": {
            "head": {"sha": HEAD},
            "base": {"sha": BASE},
            "state": "open",
            "draft": False,
            "changed_files": 1,
        },
        f"/repos/org/repo/commits/{HEAD}/check-runs": {
            "check_runs": [
                {
                    "id": 10,
                    "name": "tests",
                    "head_sha": HEAD,
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "id": 99,
                    "name": "Open SWE Review",
                    "head_sha": HEAD,
                    "status": "in_progress",
                    "conclusion": None,
                },
            ]
        },
        f"/repos/org/repo/commits/{HEAD}/status": {"sha": HEAD, "statuses": []},
        "/repos/org/repo/pulls/7/reviews": [],
        "/repos/org/repo/pulls/7/files": [{"filename": "README.md"}],
    }
    requests: list[httpx2.Request] = []

    async def request(
        client: httpx2.AsyncClient,
        method: str,
        url: str,
        *,
        params: dict[str, str | int] | None = None,
    ) -> httpx2.Response:
        req = httpx2.Request(method, url, params=params)
        requests.append(req)
        value = payloads[req.url.path]
        return httpx2.Response(
            value if isinstance(value, int) else 200,
            json={} if isinstance(value, int) else value,
            request=req,
        )

    with patch("agent.review.approval_github.github_request", side_effect=request):
        yield payloads, requests


async def evidence() -> ApprovalEvidence:
    policy = await fetch_approval_policy("org", "repo", 7, "test")
    return ApprovalEvidence(
        policy_version=policy.version,
        base_sha=BASE,
        head_sha=HEAD,
        review_complete=True,
        criteria=[
            CriterionEvidence(id="shared:scope", status="pass", evidence="README prose only.")
        ],
    )


async def evaluate(evidence: ApprovalEvidence | None) -> ApprovalEvaluation:
    return await evaluate_pr_approval(
        owner="org",
        repo="repo",
        pr_number=7,
        token="test",
        head_sha=HEAD,
        risk_score=1,
        confidence="high",
        limitations=[],
        open_findings=0,
        evidence=evidence,
        review_check_run_id=99,
    )


async def test_policy_comes_from_settings_and_gates_use_exact_reviewed_head(
    github: GithubFixture,
) -> None:
    _, requests = github
    result = await evaluate(await evidence())
    assert result.decision == "would_approve"
    assert result.policy is not None and result.policy.source == "settings"
    content_requests = [r for r in requests if "/contents" in r.url.path]
    assert not content_requests
    assert result.policy.base_sha == BASE
    assert all(r.method == "GET" for r in requests)


@pytest.mark.parametrize(
    ("status", "conclusion", "decision"),
    [
        ("completed", "failure", "needs_human_review"),
        ("in_progress", None, "insufficient_evidence"),
        ("completed", "skipped", "insufficient_evidence"),
    ],
)
async def test_ci_cannot_be_overridden_by_model_evidence(
    github: GithubFixture, status: str, conclusion: str | None, decision: ApprovalDecision
) -> None:
    payloads, _ = github
    payloads[f"/repos/org/repo/commits/{HEAD}/check-runs"] = {
        "check_runs": [
            {
                "id": 10,
                "head_sha": HEAD,
                "name": "tests",
                "status": status,
                "conclusion": conclusion,
            }
        ]
    }
    assert (await evaluate(await evidence())).decision == decision


async def test_missing_required_check_is_not_a_pass(github: GithubFixture) -> None:
    payloads, _ = github
    payloads[f"/repos/org/repo/commits/{HEAD}/check-runs"] = {"check_runs": []}
    assert (await evaluate(await evidence())).decision == "insufficient_evidence"


async def test_unreadable_policy_does_not_fall_back_to_default(
    github: GithubFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        settings.CURRENT, "get", AsyncMock(side_effect=RuntimeError("settings unavailable"))
    )
    result = await evaluate(None)
    assert result.decision == "insufficient_evidence"
    assert result.policy is None


async def test_absent_policy_uses_versioned_default(github: GithubFixture) -> None:
    await settings.CURRENT.delete("default")
    policy = await fetch_approval_policy("org", "repo", 7, "test")
    assert policy.source == "default" and policy.criteria
    assert policy.base_sha == BASE


async def test_settings_changed_since_review_invalidate_evidence(github: GithubFixture) -> None:
    old_evidence = await evidence()
    await settings.CURRENT.put(
        "default", settings.PolicyRevision(repository=None, policy=POLICY, updated_by="other-admin")
    )
    result = await evaluate(old_evidence)
    assert result.decision == "insufficient_evidence"
    assert next(g for g in result.criteria if g.id == "policy_binding").status == "unknown"


async def test_settings_changed_during_github_collection_cannot_approve(
    github: GithubFixture,
) -> None:
    old_evidence = await evidence()
    original = settings.load_policy_snapshot
    calls = 0

    async def changing_policy(repository: str, *, base_sha: str, head_sha: str):
        nonlocal calls
        calls += 1
        if calls == 2:
            await settings.CURRENT.put(
                "default",
                settings.PolicyRevision(repository=None, policy=POLICY, updated_by="other-admin"),
            )
        return await original(repository, base_sha=base_sha, head_sha=head_sha)

    with patch("agent.review.approval_github.load_policy_snapshot", side_effect=changing_policy):
        result = await evaluate(old_evidence)
    assert result.decision == "insufficient_evidence"


async def test_later_comment_does_not_clear_outstanding_change_request(
    github: GithubFixture,
) -> None:
    payloads, _ = github
    payloads["/repos/org/repo/pulls/7/reviews"] = [
        {
            "id": 1,
            "user": {"login": "alice"},
            "state": "CHANGES_REQUESTED",
            "submitted_at": "2026-09-18T10:00:00Z",
        },
        {"id": 2, "user": {"login": "alice"}, "state": "COMMENTED"},
    ]
    assert (await evaluate(await evidence())).decision == "needs_human_review"
    payloads["/repos/org/repo/pulls/7/reviews"] = [
        {
            "id": 1,
            "user": {"login": "alice"},
            "state": "CHANGES_REQUESTED",
            "submitted_at": "2026-09-18T10:00:00Z",
        },
        {
            "id": 3,
            "user": {"login": "alice"},
            "state": "APPROVED",
            "submitted_at": "2026-09-18T11:00:00Z",
        },
    ]
    assert (await evaluate(await evidence())).decision == "would_approve"


async def test_earlier_created_review_submitted_later_can_request_changes(
    github: GithubFixture,
) -> None:
    payloads, _ = github
    payloads["/repos/org/repo/pulls/7/reviews"] = [
        {
            "id": 1,
            "user": {"login": "alice"},
            "state": "CHANGES_REQUESTED",
            "submitted_at": "2026-09-18T12:00:00Z",
        },
        {
            "id": 3,
            "user": {"login": "alice"},
            "state": "APPROVED",
            "submitted_at": "2026-09-18T11:00:00Z",
        },
    ]
    assert (await evaluate(await evidence())).decision == "needs_human_review"


async def test_rename_out_of_protected_path_still_requires_human(github: GithubFixture) -> None:
    payloads, _ = github
    payloads["/repos/org/repo/pulls/7/files"] = [
        {"filename": "docs/policy.txt", "previous_filename": "auth/session.py"}
    ]
    assert (await evaluate(await evidence())).decision == "needs_human_review"


async def test_incomplete_files_or_failed_ci_read_is_unknown(github: GithubFixture) -> None:
    payloads, _ = github
    payloads["/repos/org/repo/pulls/7/files"] = []
    payloads[f"/repos/org/repo/commits/{HEAD}/check-runs"] = 403
    assert (await evaluate(await evidence())).decision == "insufficient_evidence"
