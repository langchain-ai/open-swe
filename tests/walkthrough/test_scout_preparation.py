import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from langgraph.runtime import Runtime

from openswe.github.http import GitHubError
from openswe.review_scout import graph, launch
from openswe.run_config import RunConfig
from openswe.sandboxes.repo_prep import ReviewRepoPreparationError
from openswe.tools import plan_walkthrough
from openswe.walkthrough import planner
from openswe.walkthrough.checkout import CheckoutError, CheckoutRateLimited, PinnedCheckout


@pytest.mark.parametrize(
    ("failures", "heads", "expected"),
    [
        (1, ["head", "head"], "planned"),
        (0, ["head"], "complete"),
        (1, ["head", "new"], "stale"),
        (2, ["head", "head"], "error"),
        (1, ["new"], "stale"),
        (2, ["head", "new"], "stale"),
        (0, ["new"], "stale"),
        (0, ["head"], "moved"),
        (0, ["head", "new"], "advanced"),
        (0, [RuntimeError("GitHub unavailable")], "planned"),
        (1, [RuntimeError("GitHub unavailable"), "head"], "planned"),
        (2, [RuntimeError("GitHub unavailable")] * 2, "error"),
    ],
)
async def test_scout_preparation_retries_and_checks_head_without_a_pinned_checkout(
    monkeypatch: pytest.MonkeyPatch, failures: int, heads: list[str | Exception], expected: str
) -> None:
    checkout = MagicMock(repo_dir="/work/widgets", pin=AsyncMock())
    monkeypatch.setattr(graph, "_ensure_scout_sandbox", AsyncMock())
    monkeypatch.setattr(graph.PinnedCheckout, "locate", AsyncMock(return_value=checkout))
    monkeypatch.setattr(launch.queue, "cancel_key", AsyncMock())
    prepare = AsyncMock(
        side_effect=[
            *[
                ReviewRepoPreparationError("x" * 3000 + "fatal: checkout failed")
                for _ in range(failures)
            ],
            None,
        ]
    )
    monkeypatch.setattr(graph, "prepare_review_repo_or_raise", prepare)
    pull = MagicMock()
    pull.head_sha = AsyncMock(side_effect=heads)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=pull)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(graph.PullRequestClient, "as_app", lambda *args: client)
    sleep = AsyncMock()
    monkeypatch.setattr(graph.asyncio, "sleep", sleep)
    workspace = AsyncMock(
        side_effect=graph.PlanMovedError("newer head") if expected == "moved" else None,
        return_value=MagicMock(
            complete=expected != "planned",
            pull_request=MagicMock(concise_walkthrough=AsyncMock(return_value=False)),
        ),
    )
    install = AsyncMock()
    if expected == "advanced":
        stored = MagicMock(merge_base_sha="base", head_sha="head")
        checkout.pinned = AsyncMock(return_value=("base", "head"))
        checkout.pin.side_effect = lambda **_: setattr(stored, "head_sha", "new")
        monkeypatch.setattr(planner.PullRequest, "ensure", AsyncMock(return_value=MagicMock()))
        monkeypatch.setattr(graph.Walkthrough, "get", AsyncMock(return_value=stored))
        monkeypatch.setattr(graph.Walkthrough, "install", install)
    else:
        monkeypatch.setattr(graph, "_workspace", workspace)
    monkeypatch.setattr(graph, "prompt", lambda *args, **kwargs: "scout instructions")
    monkeypatch.setattr(graph.SteeringHistory, "load", AsyncMock(return_value=None))
    middleware = graph.PrepareReviewScoutRunMiddleware(
        thread_id="scout",
        config={
            "configurable": {
                "repo": {"owner": "acme", "name": "widgets"},
                "pr_number": 7,
                "base_sha": "base",
                "head_sha": "head",
            }
        },
    )
    if expected == "error":
        with pytest.raises(RuntimeError, match="fatal: checkout failed") as error:
            await middleware._prepare({"messages": []}, Runtime())
        prefix = "review scout could not check out the pull request: "
        assert str(error.value).startswith(prefix)
        assert len(str(error.value).removeprefix(prefix)) == 2000
    else:
        result = await middleware._prepare({"messages": []}, Runtime())
        assert result["scout_nothing_to_plan"] is (expected != "planned")
        if expected == "planned":
            assert result["rendered_system_prompt"] == "scout instructions"
        assert checkout.pin.await_count == (
            1 if expected in ("planned", "complete", "moved", "advanced") else 0
        )
        assert workspace.await_count == (1 if expected in ("planned", "complete", "moved") else 0)
        if expected == "advanced":
            install.assert_not_awaited()
            assert stored.head_sha == "new"
    assert prepare.await_count == (1 if expected == "advanced" else len(heads))
    assert sleep.await_args_list == (
        [] if len(heads) == 1 or expected == "advanced" else [((3,), {})]
    )


@pytest.mark.parametrize("scout", [False, True, None])
@pytest.mark.parametrize("tool", ["plan_chunk", "split_hunks", "move_to_other"])
async def test_planning_url_does_not_route_a_scout_to_implementation(
    monkeypatch: pytest.MonkeyPatch, scout: bool | None, tool: str
) -> None:
    cfg = RunConfig.parse(
        {"thread_id": "scout", "repo": {"owner": "acme", "name": "widgets"}, "pr_number": 7}
    )
    monkeypatch.setattr(RunConfig, "from_runtime", lambda: cfg)
    monkeypatch.setattr(plan_walkthrough, "get_cached_sandbox_backend", lambda _: MagicMock())
    locate = AsyncMock(return_value=AsyncMock())
    implementation = AsyncMock(return_value=AsyncMock())
    monkeypatch.setattr(plan_walkthrough.PlanWorkspace, "locate", locate)
    monkeypatch.setattr(plan_walkthrough.PlanWorkspace, "for_implementation", implementation)
    planned = AsyncMock()
    monkeypatch.setattr(plan_walkthrough, "_planned", planned)
    pr_url = "https://github.com/acme/another-repo/pull/99"

    if tool == "plan_chunk":
        await plan_walkthrough.walkthrough_plan_chunk(
            scout=scout, tool_call_id="call", pr_url=pr_url, show=[]
        )
    elif tool == "split_hunks":
        await plan_walkthrough.walkthrough_split_hunks(
            [], scout=scout, tool_call_id="call", pr_url=pr_url
        )
    else:
        await plan_walkthrough.walkthrough_move_to_other(
            [], scout=scout, tool_call_id="call", pr_url=pr_url
        )

    if scout is None:
        implementation.assert_awaited_once_with(cfg, pr_url)
        locate.assert_not_awaited()
    else:
        implementation.assert_not_awaited()
        assert locate.await_args.kwargs == {"owner": "acme", "repo": "widgets", "number": 7}
    assert planned.await_args.args == (
        implementation.return_value if scout is None else locate.return_value,
    )


@pytest.mark.parametrize(
    ("status", "headers", "message", "wait"),
    [
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "+1800"}, "limit", 1800),
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "+7200"}, "limit", 3600),
        (403, {"Retry-After": "90"}, "You have exceeded a secondary rate limit", 90),
        (429, {}, "Too many requests", 60),
        (403, {}, "Resource not accessible by integration", None),
        (404, {}, "Not Found", None),
    ],
)
async def test_rate_limited_merge_base_delays_the_scout_and_other_refusals_fail_it(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    headers: dict[str, str],
    message: str,
    wait: int | None,
) -> None:
    if reset := headers.get("X-RateLimit-Reset"):
        headers = {**headers, "X-RateLimit-Reset": str(int(time.time()) + int(reset))}
    response = httpx2.Response(
        status,
        headers=headers,
        json={"message": message},
        request=httpx2.Request("GET", "https://api.github.com/repos/acme/widgets/compare/a...b"),
    )
    pull = MagicMock(
        head_sha=AsyncMock(return_value="head"),
        merge_base=AsyncMock(side_effect=GitHubError(response)),
    )
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=pull)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(graph.PullRequestClient, "as_app", lambda *args: client)
    backend = MagicMock()
    monkeypatch.setattr(graph, "_ensure_scout_sandbox", AsyncMock(return_value=backend))
    checkout = PinnedCheckout(backend, "/work/widgets")
    monkeypatch.setattr(graph.PinnedCheckout, "locate", AsyncMock(return_value=checkout))
    monkeypatch.setattr(graph, "prepare_review_repo_or_raise", AsyncMock())
    workspace = AsyncMock(side_effect=AssertionError("must not open the walkthrough"))
    monkeypatch.setattr(graph, "_workspace", workspace)
    schedule = AsyncMock()
    monkeypatch.setattr(launch.queue, "schedule", schedule)
    middleware = graph.PrepareReviewScoutRunMiddleware(
        thread_id="scout",
        config={
            "configurable": {
                "repo": {"owner": "acme", "name": "widgets"},
                "pr_number": 7,
                "pr_title": "Change",
                "base_sha": "base",
                "head_sha": "head",
            }
        },
    )

    before = datetime.now(UTC)
    if wait is None:
        with pytest.raises(CheckoutError, match="merge base") as error:
            await middleware._prepare({"messages": []}, Runtime())
        assert not isinstance(error.value, CheckoutRateLimited)
        schedule.assert_not_awaited()
    else:
        result = await middleware._prepare({"messages": []}, Runtime())
        assert result["scout_nothing_to_plan"] is True
        assert schedule.await_args is not None
        (kind, payload), kwargs = schedule.await_args
        assert kind == launch.ReviewScoutTarget.RETRY_JOB
        assert launch.ReviewScoutTarget.model_validate(payload).head_sha == "head"
        assert kwargs["key"] == f"{kind}:acme/widgets#7@head"
        delay = kwargs["due_at"] - before
        assert timedelta(seconds=wait - 2) <= delay <= timedelta(seconds=wait + 2)
    workspace.assert_not_awaited()


@pytest.mark.parametrize("delayed", [False, True])
async def test_a_delayed_scout_leaves_its_plan_to_the_retry(
    monkeypatch: pytest.MonkeyPatch, delayed: bool
) -> None:
    monkeypatch.setattr(
        graph.ReviewScoutTarget,
        "delayed_until",
        AsyncMock(return_value=datetime.now(UTC) if delayed else None),
    )
    monkeypatch.setattr(graph, "get_cached_sandbox_backend", lambda _: MagicMock())
    workspace = MagicMock(settle=AsyncMock(return_value=0), plan=MagicMock(chunks=[]))
    monkeypatch.setattr(graph, "_workspace", AsyncMock(return_value=workspace))
    monkeypatch.setattr(graph.Topic.PULL_REQUESTS, "invalidate", AsyncMock())

    await graph.finish_scout_plan(
        "scout",
        {
            "configurable": {
                "repo": {"owner": "acme", "name": "widgets"},
                "pr_number": 7,
                "base_sha": "base",
                "head_sha": "head",
            }
        },
        "",
    )

    assert workspace.settle.await_count == (0 if delayed else 1)


async def test_a_scout_that_gets_past_the_limit_cancels_the_queued_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = MagicMock(repo_dir="/work/widgets", pin=AsyncMock())
    monkeypatch.setattr(graph, "_ensure_scout_sandbox", AsyncMock())
    monkeypatch.setattr(graph.PinnedCheckout, "locate", AsyncMock(return_value=checkout))
    monkeypatch.setattr(graph, "prepare_review_repo_or_raise", AsyncMock())
    pull = MagicMock(head_sha=AsyncMock(return_value="head"))
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=pull)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(graph.PullRequestClient, "as_app", lambda *args: client)
    monkeypatch.setattr(graph, "_workspace", AsyncMock(return_value=None))
    cancel_key = AsyncMock()
    monkeypatch.setattr(launch.queue, "cancel_key", cancel_key)
    middleware = graph.PrepareReviewScoutRunMiddleware(
        thread_id="scout",
        config={
            "configurable": {
                "repo": {"owner": "acme", "name": "widgets"},
                "pr_number": 7,
                "base_sha": "base",
                "head_sha": "head",
            }
        },
    )

    await middleware._prepare({"messages": []}, Runtime())

    cancel_key.assert_awaited_once_with(f"{launch.ReviewScoutTarget.RETRY_JOB}:acme/widgets#7@head")


@pytest.mark.parametrize(
    ("status", "headers", "requeued"),
    [
        (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "+1800"}, True),
        (403, {"Retry-After": "90"}, True),
        (500, {}, False),
    ],
)
async def test_a_still_rate_limited_retry_queues_itself_for_githubs_reset(
    monkeypatch: pytest.MonkeyPatch, status: int, headers: dict[str, str], requeued: bool
) -> None:
    if reset := headers.get("X-RateLimit-Reset"):
        headers = {**headers, "X-RateLimit-Reset": str(int(time.time()) + int(reset))}
    response = httpx2.Response(
        status,
        headers=headers,
        json={"message": "limit"},
        request=httpx2.Request("GET", "https://api.github.com/repos/acme/widgets/pulls/7"),
    )
    pull = MagicMock(head_sha=AsyncMock(side_effect=GitHubError(response)))
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=pull)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(launch.PullRequestClient, "as_app", lambda *args: client)
    schedule = AsyncMock()
    monkeypatch.setattr(launch.queue, "schedule", schedule)
    start = AsyncMock()
    monkeypatch.setattr(launch.ReviewScoutTarget, "start", start)
    target = launch.ReviewScoutTarget(
        owner="acme",
        repo="widgets",
        pr_number=7,
        pr_title="Change",
        base_sha="base",
        head_sha="head",
    )
    job = MagicMock(payload=target.model_dump(mode="json"))

    before = datetime.now(UTC)
    if requeued:
        await launch.ReviewScoutTarget.retry(job)
        assert schedule.await_args is not None
        kwargs = schedule.await_args.kwargs
        assert kwargs["key"] == f"{launch.ReviewScoutTarget.RETRY_JOB}:acme/widgets#7@head"
        assert kwargs["due_at"] - before >= timedelta(seconds=60)
    else:
        with pytest.raises(GitHubError):
            await launch.ReviewScoutTarget.retry(job)
        schedule.assert_not_awaited()
    start.assert_not_awaited()
