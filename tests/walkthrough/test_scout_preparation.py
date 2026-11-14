from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.runtime import Runtime

from openswe.github import app
from openswe.review_scout import graph
from openswe.run_config import RunConfig
from openswe.sandboxes.repo_prep import ReviewRepoPreparationError
from openswe.tools import plan_walkthrough


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
        (0, [RuntimeError("GitHub unavailable")], "planned"),
        (1, [RuntimeError("GitHub unavailable"), "head"], "planned"),
        (2, [RuntimeError("GitHub unavailable")] * 2, "error"),
    ],
)
async def test_scout_preparation_retries_and_checks_head_without_a_pinned_checkout(
    monkeypatch: pytest.MonkeyPatch, failures: int, heads: list[str | Exception], expected: str
) -> None:
    checkout = MagicMock(repo_dir="/work/widgets", pin=AsyncMock())
    monkeypatch.setattr(app, "GITHUB_APP_INSTALLATION_ID", "")
    monkeypatch.setattr(app, "_local_dev_token", AsyncMock(return_value=None))
    monkeypatch.setattr(graph, "ensure_sandbox_for_thread", AsyncMock())
    monkeypatch.setattr(graph.PinnedCheckout, "locate", AsyncMock(return_value=checkout))
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
            1 if expected in ("planned", "complete", "moved") else 0
        )
        assert workspace.await_count == (1 if expected in ("planned", "complete", "moved") else 0)
    assert prepare.await_count == len(heads)
    assert sleep.await_args_list == ([] if len(heads) == 1 else [((3,), {})])


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
