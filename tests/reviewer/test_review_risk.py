from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from agent.review import risk
from agent.review.approval import unavailable_evaluation
from agent.review.findings import new_finding
from tests.conftest import FakeStore


@pytest.fixture
def publication(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    import importlib

    module = importlib.import_module("agent.tools.publish_review")
    monkeypatch.setattr(module, "get_thread_id_from_runtime", lambda: "reviewer-thread")
    monkeypatch.setattr(
        module, "dashboard_review_url", lambda *args: "https://example.test/review/org/repo/7"
    )
    monkeypatch.setattr(module.PullRequest, "link_review", AsyncMock())
    for name, result in (
        ("resolve_review_head_sha", "a" * 40),
        ("list_findings_async", []),
        ("fetch_pr_review_threads", []),
        ("set_reviewer_thread_metadata", None),
        ("settle_review_check_run", None),
        ("clear_review_started_comment", None),
        ("_record_reviewer_usage", None),
        ("find_published_assessment", None),
        ("get_thread_metadata", {}),
        ("evaluate_pr_approval", unavailable_evaluation("No policy evidence provided.")),
    ):
        monkeypatch.setattr(module, name, AsyncMock(return_value=result))
    post = AsyncMock(return_value={"id": 123})
    monkeypatch.setattr(module, "post_pull_request_review", post)
    return post


async def publish(assessment: risk.RiskInput, run_id: str | None = "run") -> dict[str, object]:
    from agent.tools.publish_review import _publish_review_async

    return await _publish_review_async(
        owner="org",
        repo="repo",
        pr_number=7,
        head_sha="a" * 40,
        token="test",
        severity_threshold="medium",
        cap=None,
        is_re_review=True,
        langgraph_run_id=run_id,
        trace_link_config_override=False,
        risk_assessment=assessment,
    )


def assessment_input(head_sha: str = "a" * 40) -> risk.RiskInput:
    return risk.RiskInput(
        head_sha=head_sha,
        score=2,
        confidence="high",
        rationale="Localized behavior change with focused tests.",
        limitations=[],
    )


async def test_existing_unresolved_finding_prevents_low_risk_score(fake_store: FakeStore) -> None:
    finding = new_finding(
        severity="critical",
        confidence="high",
        category="security",
        file="auth.py",
        start_line=1,
        end_line=1,
        description="Authorization is bypassed.",
        sha="old",
    )
    finding["github_review_comment_ids"] = [123]
    record = await risk.prepare_assessment(
        owner="org",
        repo="repo",
        pr_number=7,
        head_sha="a" * 40,
        run_id="run",
        assessment=assessment_input(),
        findings=[finding],
    )
    assert record.score == 5
    assert record.open_findings == 1
    assert record.proposed_score == 2


async def test_stale_assessment_cannot_be_attached_to_new_commit(fake_store: FakeStore) -> None:
    with pytest.raises(ValueError, match="commit"):
        await risk.prepare_assessment(
            owner="org",
            repo="repo",
            pr_number=7,
            head_sha="b" * 40,
            run_id="run",
            assessment=assessment_input(),
            findings=[],
        )
    assert not fake_store.values(["review_risk_assessments"])


async def test_retries_preserve_original_score_and_feedback(fake_store: FakeStore) -> None:
    original = await risk.prepare_assessment(
        owner="org",
        repo="repo",
        pr_number=7,
        head_sha="a" * 40,
        run_id="run",
        assessment=assessment_input(),
        findings=[],
    )
    original.github_review_id = 123
    await risk.ASSESSMENTS.put(original.id, original)
    await risk.save_feedback(
        original,
        login="alice",
        submission=risk.RiskFeedbackSubmission(
            decision="needs_review", comment="Touches a shared contract."
        ),
    )
    changed = assessment_input().model_copy(update={"score": 4})
    retried = await risk.prepare_assessment(
        owner="org",
        repo="repo",
        pr_number=7,
        head_sha="a" * 40,
        run_id="run",
        assessment=changed,
        findings=[],
    )
    assert retried == original
    feedback = await risk.get_feedback(original.id, "alice")
    assert feedback is not None
    assert feedback.decision == "needs_review"
    assert feedback.assessment_id == original.id


async def test_feedback_is_per_person_and_per_assessment(fake_store: FakeStore) -> None:
    records = [
        await risk.prepare_assessment(
            owner="org",
            repo="repo",
            pr_number=7,
            head_sha=sha * 40,
            run_id="run",
            assessment=assessment_input(sha * 40),
            findings=[],
        )
        for sha in ("a", "b")
    ]
    await risk.save_feedback(
        records[0], login="alice", submission=risk.RiskFeedbackSubmission(decision="safe")
    )
    await risk.save_feedback(
        records[0], login="bob", submission=risk.RiskFeedbackSubmission(decision="needs_review")
    )
    await risk.save_feedback(
        records[1], login="alice", submission=risk.RiskFeedbackSubmission(decision="unsure")
    )
    await risk.save_feedback(
        records[0], login="alice", submission=risk.RiskFeedbackSubmission(decision="needs_review")
    )
    assert len(fake_store.values(["review_risk_feedback"])) == 3
    feedback = await risk.get_feedback(records[0].id, "alice")
    assert feedback is not None and feedback.decision == "needs_review"
    newer = await risk.get_feedback(records[1].id, "alice")
    assert newer is not None and newer.decision == "unsure"


async def test_assessment_cannot_be_read_through_another_pr(fake_store: FakeStore) -> None:
    record = await risk.prepare_assessment(
        owner="org",
        repo="private",
        pr_number=7,
        head_sha="a" * 40,
        run_id="run",
        assessment=assessment_input(),
        findings=[],
    )
    assert await risk.get_assessment("org", "public", 7, record.id) is None
    assert await risk.get_assessment("org", "private", 8, record.id) is None


async def test_feedback_api_requires_repo_access_before_reading_assessment() -> None:
    from agent.review.routes import api_submit_risk_feedback

    with (
        patch(
            "agent.review.routes.require_repo_access_for_user",
            AsyncMock(side_effect=HTTPException(403)),
        ),
        patch(
            "agent.review.routes.risk.get_assessment",
            AsyncMock(side_effect=AssertionError("unauthorized read")),
        ),
    ):
        with pytest.raises(HTTPException) as error:
            await api_submit_risk_feedback(
                "org",
                "repo",
                7,
                "assessment",
                risk.RiskFeedbackSubmission(decision="safe"),
                session={"sub": "alice"},
            )
    assert error.value.status_code == 403


async def test_empty_rereview_publishes_new_score_once(
    fake_store: FakeStore, publication: AsyncMock
) -> None:
    first = await publish(assessment_input())
    assert first["review_id"] == 123
    saved = await risk.ASSESSMENTS.search_all()
    assert len(saved) == 1
    assert saved[0].github_review_id == 123
    assert saved[0].head_sha == "a" * 40
    assert saved[0].id in publication.call_args.kwargs["body"]
    assert publication.call_args.kwargs["head_sha"] == "a" * 40
    retry = await publish(assessment_input())
    assert retry["skipped_empty_re_review"] is True
    assert publication.await_count == 1


async def test_same_commit_rereviews_use_current_run_when_runtime_id_missing(
    fake_store: FakeStore, publication: AsyncMock
) -> None:
    with patch(
        "agent.tools.publish_review.get_thread_metadata",
        AsyncMock(
            side_effect=[
                {"current_reviewer_run_id": "first-run"},
                {"current_reviewer_run_id": "second-run"},
            ]
        ),
    ):
        await publish(assessment_input(), run_id=None)
        await publish(assessment_input().model_copy(update={"score": 3}), run_id=None)
    records = await risk.ASSESSMENTS.search_all()
    assert {(record.run_id, record.score) for record in records} == {
        ("first-run", 2),
        ("second-run", 3),
    }
    assert len({record.id for record in records}) == 2
    assert publication.await_count == 2


async def test_failed_publication_is_not_available_for_feedback(
    fake_store: FakeStore, publication: AsyncMock
) -> None:
    from agent.review.routes import api_get_review_risk, api_submit_risk_feedback

    publication.return_value = {"_error": "GitHub unavailable"}
    result = await publish(assessment_input())
    assert result["success"] is False
    saved = await risk.ASSESSMENTS.search_all()
    assert len(saved) == 1 and saved[0].github_review_id is None
    with patch("agent.review.routes.require_repo_access_for_user", AsyncMock()):
        with pytest.raises(HTTPException) as read_error:
            await api_get_review_risk("org", "repo", 7, saved[0].id, session={"sub": "alice"})
        with pytest.raises(HTTPException) as write_error:
            await api_submit_risk_feedback(
                "org",
                "repo",
                7,
                saved[0].id,
                risk.RiskFeedbackSubmission(decision="safe"),
                session={"sub": "alice"},
            )
    assert read_error.value.status_code == write_error.value.status_code == 404


async def test_stale_score_stops_publication(fake_store: FakeStore, publication: AsyncMock) -> None:
    result = await publish(assessment_input("b" * 40))
    assert result["success"] is False
    assert publication.await_count == 0


async def test_unknown_risk_is_preserved(fake_store: FakeStore, publication: AsyncMock) -> None:
    assessment = assessment_input().model_copy(update={"score": None, "confidence": "low"})
    await publish(assessment)
    record = (await risk.ASSESSMENTS.search_all())[0]
    assert record.score is None
    assert "Not assessed" in publication.call_args.kwargs["body"]


async def test_unpublished_assessment_is_recomputed_after_finding_is_resolved(
    fake_store: FakeStore,
) -> None:
    finding = new_finding(
        severity="critical",
        confidence="high",
        category="security",
        file="auth.py",
        start_line=1,
        end_line=1,
        description="Broken authorization",
        sha="a" * 40,
    )
    first = await risk.prepare_assessment(
        owner="org",
        repo="repo",
        pr_number=7,
        head_sha="a" * 40,
        run_id="run",
        assessment=assessment_input(),
        findings=[finding],
    )
    assert first.score == 5
    finding["status"] = "dismissed"
    retry = await risk.prepare_assessment(
        owner="org",
        repo="repo",
        pr_number=7,
        head_sha="a" * 40,
        run_id="run",
        assessment=assessment_input(),
        findings=[finding],
    )
    assert retry.score == 2 and retry.open_findings == 0


async def test_store_failure_after_post_does_not_repost_review(
    fake_store: FakeStore, publication: AsyncMock
) -> None:
    import importlib

    module = importlib.import_module("agent.tools.publish_review")
    put = risk.ASSESSMENTS.put
    fail_once = True

    async def failing_put(key: str, record: risk.RiskAssessment) -> risk.RiskAssessment:
        nonlocal fail_once
        if record.github_review_id is not None and fail_once:
            fail_once = False
            raise OSError("Store unavailable")
        return await put(key, record)

    with patch.object(risk.ASSESSMENTS, "put", failing_put):
        with pytest.raises(OSError):
            await publish(assessment_input())
        with patch.object(module, "find_published_assessment", AsyncMock(return_value=123)):
            retry = await publish(assessment_input())
    assert retry["success"] is True
    assert retry["review_id"] == 123
    assert retry["reused_existing_review"] is True
    assert publication.await_count == 1
    record = (await risk.ASSESSMENTS.search_all())[0]
    assert record.github_review_id == 123


async def test_feedback_tool_rejects_public_thread_actor(fake_store: FakeStore) -> None:
    from agent.tools.submit_review_risk_feedback import submit_review_risk_feedback

    with patch(
        "agent.tools.submit_review_risk_feedback.private_credential_login",
        AsyncMock(return_value=None),
    ):
        with pytest.raises(ValueError, match="private"):
            await submit_review_risk_feedback("org", "repo", 7, "assessment", "safe")
    assert not fake_store.values(["review_risk_feedback"])


async def test_feedback_tool_attributes_only_authenticated_owner(
    fake_store: FakeStore, publication: AsyncMock
) -> None:
    from agent.tools.submit_review_risk_feedback import submit_review_risk_feedback

    await publish(assessment_input())
    record = (await risk.ASSESSMENTS.search_all())[0]
    with (
        patch(
            "agent.tools.submit_review_risk_feedback.private_credential_login",
            AsyncMock(return_value="Alice"),
        ),
        patch("agent.tools.submit_review_risk_feedback.require_repo_access_for_user", AsyncMock()),
    ):
        result = await submit_review_risk_feedback(
            "org", "repo", 7, record.id, "needs_review", "Shared contract"
        )
    assert result["decision"] == "needs_review"
    saved = await risk.get_feedback(record.id, "alice")
    assert saved is not None and saved.login == "alice" and saved.comment == "Shared contract"


async def test_recovery_ignores_matching_marker_on_wrong_commit(fake_store: FakeStore) -> None:
    record = await risk.prepare_assessment(
        owner="org",
        repo="repo",
        pr_number=7,
        head_sha="a" * 40,
        run_id="run",
        assessment=assessment_input(),
        findings=[],
    )
    body = risk.render_risk_assessment(record, None)
    response = MagicMock(status_code=200)
    response.json.return_value = [
        {"id": 100, "body": body, "commit_id": "b" * 40},
        {"id": 101, "body": body, "commit_id": "a" * 40},
    ]
    with (
        patch("agent.review.risk.github_client", return_value=AsyncMock()),
        patch("agent.review.risk.github_request", AsyncMock(return_value=response)),
    ):
        assert await risk.find_published_assessment(record, "test") == 101


async def test_recovery_preserves_github_auth_error_handling(fake_store: FakeStore) -> None:
    from agent.github.thread_token import GitHubAuthError

    record = await risk.prepare_assessment(
        owner="org",
        repo="repo",
        pr_number=7,
        head_sha="a" * 40,
        run_id="run",
        assessment=assessment_input(),
        findings=[],
    )
    response = MagicMock(status_code=401)
    with (
        patch("agent.review.risk.github_client", return_value=AsyncMock()),
        patch("agent.review.risk.github_request", AsyncMock(return_value=response)),
    ):
        with pytest.raises(GitHubAuthError):
            await risk.find_published_assessment(record, "test")


async def test_partial_inline_publication_defers_score_until_reconciliation(
    fake_store: FakeStore, publication: AsyncMock
) -> None:
    import importlib

    from agent.review.publish import render_inline_comment_payload

    module = importlib.import_module("agent.tools.publish_review")
    findings = [
        new_finding(
            severity=severity,
            confidence="high",
            category="correctness",
            file=f"{severity}.py",
            start_line=1,
            end_line=1,
            description="Concrete defect",
            sha="a" * 40,
        )
        for severity in ("critical", "medium")
    ]
    valid_payload = render_inline_comment_payload(findings[1])
    assert valid_payload is not None
    publication.side_effect = [
        {"_error": "Invalid anchor", "_error_kind": "unresolved_anchor"},
        {"id": 100},
        {"id": 101},
    ]
    with (
        patch.object(module, "list_findings_async", AsyncMock(return_value=findings)),
        patch.object(module, "fetch_review_comments", AsyncMock(return_value=[])),
        patch.object(module, "replace_findings", AsyncMock()),
        patch.object(module, "settle_review_check_run", AsyncMock()) as settle,
        patch.object(module, "set_reviewer_thread_metadata", AsyncMock()) as metadata,
        patch.object(
            module,
            "_filter_against_pr_diff",
            AsyncMock(return_value=([(findings[1], valid_payload)], [findings[0]["id"]])),
        ),
    ):
        partial = await publish(assessment_input())
        assert partial["success"] is False
        assert partial["risk_assessment_deferred"] is True
        assert partial["unresolvable_findings"] == [findings[0]["id"]]
        assert "open-swe-review-risk" not in publication.call_args_list[1].kwargs["body"]
        assert (await risk.ASSESSMENTS.search_all())[0].github_review_id is None
        settle.assert_not_awaited()
        assert not any("last_reviewed_sha" in call.kwargs for call in metadata.await_args_list)
        findings[0]["status"] = "dismissed"
        findings[1]["github_review_comment_ids"] = [999]
        completed = await publish(assessment_input())
        settle.assert_awaited_once()
    assert completed["review_id"] == 101
    saved = (await risk.ASSESSMENTS.search_all())[0]
    assert saved.score == 3 and saved.open_findings == 1
    assert saved.publication_complete is True


async def test_feedback_http_roundtrip_and_csrf(
    fake_store: FakeStore, publication: AsyncMock
) -> None:
    import httpx
    from fastapi import FastAPI

    from agent.dashboard.oauth import require_session
    from agent.review.routes import router

    await publish(assessment_input())
    record = (await risk.ASSESSMENTS.search_all())[0]
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_session] = lambda: {"sub": "alice"}
    base = f"/reviews/org/repo/7/risk/{record.id}/feedback"
    with (
        patch("agent.review.routes.require_repo_access_for_user", AsyncMock()),
        patch(
            "agent.dashboard.oauth.allowed_dashboard_origins",
            return_value={"https://dashboard.example"},
        ),
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://dashboard.example"
        ) as client:
            rejected = await client.post(
                base, json={"decision": "safe"}, headers={"Origin": "https://untrusted.example"}
            )
            assert rejected.status_code == 403
            assert await risk.get_feedback(record.id, "alice") is None
            accepted = await client.post(
                base,
                json={"decision": "needs_review", "comment": "  Shared contract  "},
                headers={"Origin": "https://dashboard.example"},
            )
            assert accepted.status_code == 200
            read = await client.get(f"/reviews/org/repo/7/risk?assessment_id={record.id}")
    assert read.json()["feedback"]["comment"] == "Shared contract"
    assert read.json()["feedback"]["assessment_id"] == record.id
