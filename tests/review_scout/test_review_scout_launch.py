from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openswe.github.token_scope import GITHUB_TOKEN_REPOSITORIES_KEY
from openswe.review.session import REVIEW_SCOUT_SOURCE
from openswe.review_scout import launch
from openswe.review_scout.launch import ReviewScoutTarget


async def test_scout_runs_as_the_requester_with_a_token_for_the_pull_requests_repository() -> None:
    target = ReviewScoutTarget(
        owner="acme",
        repo="api",
        pr_number=7,
        pr_title="Add retries",
        base_sha="base",
        head_sha="head",
        workspace_slug="oss",
    )
    create_thread = AsyncMock()
    create_run = AsyncMock(return_value={"run_id": "run-1"})
    with (
        patch.object(ReviewScoutTarget, "active_run", AsyncMock(return_value=None)),
        patch.object(launch, "dispatch_client", return_value=SimpleNamespace()),
        patch.object(
            launch,
            "cached_workspace_settings",
            AsyncMock(return_value=SimpleNamespace(review_scout_model=("model", "low"))),
        ),
        patch.object(launch, "create_thread", create_thread),
        patch.object(launch, "create_durable_run", create_run),
    ):
        assert await target.start("alice") == "run-1"

    assert create_thread.await_args is not None
    assert create_thread.await_args.kwargs["metadata"] == {
        GITHUB_TOKEN_REPOSITORIES_KEY: ["acme/api"]
    }
    assert create_run.await_args is not None
    assert create_run.await_args.args == (target.thread_id, "agent")
    configurable = create_run.await_args.kwargs["config"]["configurable"]
    assert configurable["source"] == REVIEW_SCOUT_SOURCE
    assert configurable["github_login"] == "alice"
