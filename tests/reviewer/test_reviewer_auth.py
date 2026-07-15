from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.review.auth import (
    REVIEWER_GITHUB_TOKEN_PERMISSIONS,
    ReviewerPublicationContextError,
    ReviewerPublicationContextUnavailableError,
    ReviewerTokenUnavailableError,
    invalidate_reviewer_github_token,
    mint_reviewer_github_token,
    recover_reviewer_github_token,
)


async def test_mint_reviewer_token_is_repo_scoped_and_least_privilege() -> None:
    mint = AsyncMock(return_value=("token", "expiry"))
    cache = MagicMock()
    with (
        patch("agent.review.auth.get_github_app_installation_token_with_expiry", mint),
        patch("agent.review.auth.cache_github_token_for_thread", cache),
    ):
        token = await mint_reviewer_github_token(thread_id="thread", repo="repo")

    assert token == "token"
    mint.assert_awaited_once_with(
        repositories=["repo"],
        permissions=REVIEWER_GITHUB_TOKEN_PERMISSIONS,
    )
    cache.assert_called_once_with(
        "thread",
        "token",
        expires_at="expiry",
        is_bot_token=True,
    )


async def test_mint_reviewer_token_retries_only_token_acquisition() -> None:
    mint = AsyncMock(side_effect=[(None, None), (None, None), ("token", "expiry")])
    sleep = AsyncMock()
    with (
        patch("agent.review.auth.get_github_app_installation_token_with_expiry", mint),
        patch("agent.review.auth.cache_github_token_for_thread"),
        patch("agent.review.auth.asyncio.sleep", sleep),
    ):
        token = await mint_reviewer_github_token(
            thread_id="thread",
            repo="repo",
            attempts=3,
        )

    assert token == "token"
    assert mint.await_count == 3
    assert sleep.await_count == 2


def test_invalidate_reviewer_token_targets_exact_scope() -> None:
    with patch("agent.review.auth.invalidate_cached_app_token") as invalidate:
        invalidate_reviewer_github_token("repo")

    invalidate.assert_called_once_with(
        repositories=["repo"],
        permissions=REVIEWER_GITHUB_TOKEN_PERMISSIONS,
    )


async def test_recover_reviewer_token_always_derives_scoped_app_token() -> None:
    config = {"configurable": {"thread_id": "thread"}}
    metadata = {
        "kind": "reviewer",
        "pr": {"owner": "acme", "name": "repo", "number": 7},
        "current_reviewer_run_id": "run-1",
    }
    mint = AsyncMock(return_value="scoped-app-token")
    with (
        patch("agent.review.auth.get_thread_metadata_strict", AsyncMock(return_value=metadata)),
        patch("agent.review.auth.mint_reviewer_github_token", mint),
    ):
        token = await recover_reviewer_github_token(
            run_config=config,
            thread_id="thread",
            owner="acme",
            repo="repo",
            pr_number=7,
            run_id="run-1",
        )

    assert token == "scoped-app-token"
    mint.assert_awaited_once_with(thread_id="thread", repo="repo", attempts=3)


async def test_recover_reviewer_token_after_durable_resume() -> None:
    config = {"configurable": {"thread_id": "thread"}}
    metadata = {
        "kind": "reviewer",
        "pr": {"owner": "acme", "name": "repo", "number": 7},
        "current_reviewer_run_id": "run-1",
    }
    mint = AsyncMock(return_value="recovered")
    with (
        patch("agent.review.auth.get_thread_metadata_strict", AsyncMock(return_value=metadata)),
        patch("agent.review.auth.mint_reviewer_github_token", mint),
    ):
        token = await recover_reviewer_github_token(
            run_config=config,
            thread_id="thread",
            owner="acme",
            repo="repo",
            pr_number=7,
            run_id="run-1",
        )

    assert token == "recovered"
    mint.assert_awaited_once_with(thread_id="thread", repo="repo", attempts=3)


async def test_recover_reviewer_token_when_retry_attempt_omits_runtime_run_id() -> None:
    metadata = {
        "kind": "reviewer",
        "pr": {"owner": "acme", "name": "repo", "number": 7},
        "current_reviewer_run_id": "run-1",
    }
    mint = AsyncMock(return_value="recovered")
    with (
        patch("agent.review.auth.get_thread_metadata_strict", AsyncMock(return_value=metadata)),
        patch("agent.review.auth.mint_reviewer_github_token", mint),
    ):
        token = await recover_reviewer_github_token(
            run_config={"configurable": {"thread_id": "thread"}},
            thread_id="thread",
            owner="acme",
            repo="repo",
            pr_number=7,
            run_id=None,
        )

    assert token == "recovered"
    mint.assert_awaited_once_with(thread_id="thread", repo="repo", attempts=3)


@pytest.mark.parametrize(
    "metadata",
    [
        {"kind": "agent", "pr": {"owner": "acme", "name": "repo", "number": 7}},
        {"kind": "reviewer", "pr": {"owner": "other", "name": "repo", "number": 7}},
        {"kind": "reviewer", "pr": {"owner": "acme", "name": "other", "number": 7}},
        {"kind": "reviewer", "pr": {"owner": "acme", "name": "repo", "number": 8}},
    ],
)
async def test_recover_reviewer_token_rejects_context_mismatch(
    metadata: dict[str, object],
) -> None:
    mint = AsyncMock()
    with (
        patch("agent.review.auth.get_thread_metadata_strict", AsyncMock(return_value=metadata)),
        patch("agent.review.auth.mint_reviewer_github_token", mint),
    ):
        with pytest.raises(ReviewerPublicationContextError):
            await recover_reviewer_github_token(
                run_config={"configurable": {"thread_id": "thread"}},
                thread_id="thread",
                owner="acme",
                repo="repo",
                pr_number=7,
                run_id="run-1",
            )

    mint.assert_not_awaited()


async def test_recover_reviewer_token_rejects_stale_run() -> None:
    metadata = {
        "kind": "reviewer",
        "pr": {"owner": "acme", "name": "repo", "number": 7},
        "current_reviewer_run_id": "run-2",
    }
    with (
        patch("agent.review.auth.get_thread_metadata_strict", AsyncMock(return_value=metadata)),
        patch("agent.review.auth.mint_reviewer_github_token", new_callable=AsyncMock) as mint,
    ):
        with pytest.raises(ReviewerPublicationContextError):
            await recover_reviewer_github_token(
                run_config={"configurable": {"thread_id": "thread"}},
                thread_id="thread",
                owner="acme",
                repo="repo",
                pr_number=7,
                run_id="run-1",
            )

    mint.assert_not_awaited()


async def test_recover_reviewer_token_reports_transient_metadata_failure() -> None:
    with patch(
        "agent.review.auth.get_thread_metadata_strict",
        AsyncMock(side_effect=TimeoutError("metadata unavailable")),
    ):
        with pytest.raises(ReviewerPublicationContextUnavailableError):
            await recover_reviewer_github_token(
                run_config={"configurable": {"thread_id": "thread"}},
                thread_id="thread",
                owner="acme",
                repo="repo",
                pr_number=7,
                run_id=None,
            )


async def test_recover_reviewer_token_retries_missing_active_run_context() -> None:
    run_id = "run-1"
    current_run_id = None
    metadata = {
        "kind": "reviewer",
        "pr": {"owner": "acme", "name": "repo", "number": 7},
        "current_reviewer_run_id": current_run_id,
    }
    with (
        patch("agent.review.auth.get_thread_metadata_strict", AsyncMock(return_value=metadata)),
        patch("agent.review.auth.mint_reviewer_github_token", new_callable=AsyncMock) as mint,
    ):
        with pytest.raises(ReviewerPublicationContextUnavailableError):
            await recover_reviewer_github_token(
                run_config={"configurable": {"thread_id": "thread"}},
                thread_id="thread",
                owner="acme",
                repo="repo",
                pr_number=7,
                run_id=run_id,
            )

    mint.assert_not_awaited()


async def test_recover_reviewer_token_raises_after_bounded_mint_failure() -> None:
    metadata = {
        "kind": "reviewer",
        "pr": {"owner": "acme", "name": "repo", "number": 7},
        "current_reviewer_run_id": "run-1",
    }
    with (
        patch("agent.review.auth.get_thread_metadata_strict", AsyncMock(return_value=metadata)),
        patch(
            "agent.review.auth.mint_reviewer_github_token",
            AsyncMock(return_value=None),
        ) as mint,
    ):
        with pytest.raises(ReviewerTokenUnavailableError):
            await recover_reviewer_github_token(
                run_config={"configurable": {"thread_id": "thread"}},
                thread_id="thread",
                owner="acme",
                repo="repo",
                pr_number=7,
                run_id="run-1",
            )

    mint.assert_awaited_once_with(thread_id="thread", repo="repo", attempts=3)
