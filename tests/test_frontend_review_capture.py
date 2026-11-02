from unittest.mock import AsyncMock, Mock
from uuid import uuid7

import pytest

from openswe.frontend_review import capture
from openswe.frontend_review.models import FrontendReviewRun
from openswe.github.pull_requests import PullRequest
from openswe.run_config import RunConfig
from openswe.tools import frontend_review as frontend_review_tools


async def test_anonymous_capture_finishes_blocked_before_provider_access_or_browser_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = FrontendReviewRun(
        id=uuid7(),
        pull_request_id=uuid7(),
        base_sha="a" * 40,
        head_sha="b" * 40,
        base_url="https://base.vercel.app",
        head_url="https://head.vercel.app",
        paths=["/"],
        auth_mode="none",
    )
    run.pull_request = PullRequest(owner="acme", repo="widgets", number=7)
    finish = AsyncMock()
    monkeypatch.setattr(capture.FrontendReviews, "finish", finish)
    provider = Mock(side_effect=AssertionError("must not access browser service credentials"))
    monkeypatch.setattr(capture.SandboxAPI, "from_env", provider)

    await capture.capture_run(run)

    assert not provider.called
    assert finish.call_args.kwargs["status"] == "blocked"
    assert "browser service enforces network isolation" in finish.call_args.kwargs["error"]
    assert finish.call_args.kwargs["assets"] == []


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/",
        "https://metadata.google.internal/",
        "https://smith.langchain.com.evil.test/",
        "https://preview.vercel.app/?token=secret",
        "https://user:password@preview.vercel.app/",
    ],
)
def test_capture_refuses_unapproved_or_credential_bearing_urls(url: str) -> None:
    with pytest.raises(capture.CaptureBlockedError):
        capture.validate_preview_url(url)


async def test_a_capture_needs_a_run_bound_to_a_pull_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RunConfig, "from_runtime", lambda: RunConfig.parse({"thread_id": "t-1"}))

    with pytest.raises(ValueError, match="bound to a repository and pull request"):
        await frontend_review_tools.review_frontend(
            "https://base.vercel.app", "https://head.vercel.app"
        )


async def test_a_run_cannot_read_evidence_from_another_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid7()
    evidence = FrontendReviewRun(
        id=run_id,
        pull_request_id=uuid7(),
        base_sha="a" * 40,
        head_sha="c" * 40,
        base_url="https://base.vercel.app",
        head_url="https://head.vercel.app",
        paths=["/"],
        auth_mode="none",
    )
    monkeypatch.setattr(
        frontend_review_tools.FrontendReviews, "get", AsyncMock(return_value=evidence)
    )
    monkeypatch.setattr(
        RunConfig,
        "from_runtime",
        lambda: RunConfig.parse(
            {
                "thread_id": "thread-1",
                "repo": {"owner": "acme", "name": "widgets"},
                "pr_number": 7,
                "head_sha": "b" * 40,
            }
        ),
    )
    with pytest.raises(ValueError, match="does not belong"):
        await frontend_review_tools.read_frontend_review(str(run_id))
