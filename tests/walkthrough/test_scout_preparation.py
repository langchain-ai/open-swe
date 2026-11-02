from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.runtime import Runtime

from openswe.review_scout import graph
from openswe.sandboxes.repo_prep import ReviewRepoPreparationError


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
    monkeypatch.setattr(graph, "_ensure_scout_sandbox", AsyncMock())
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
        return_value=MagicMock(complete=expected != "planned"),
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


class TestScoutGitHubAccess:
    @pytest.mark.parametrize("installation_id", [None, 456])
    async def test_token_unavailable_skips_then_retries_without_creating_a_sandbox(
        self, monkeypatch: pytest.MonkeyPatch, installation_id: int | None
    ) -> None:
        from openswe.github import thread_token

        mint = AsyncMock(side_effect=[(None, None), ("scout-token", None)])
        sandbox = AsyncMock(return_value=MagicMock())
        monkeypatch.setattr(graph, "get_github_app_installation_token_with_expiry", mint)
        monkeypatch.setattr(graph, "ensure_sandbox_for_thread", sandbox)
        cfg = graph.RunConfig.parse(
            {
                "repo": {"owner": "acme", "name": "widgets"},
                "pr_number": 7,
                "base_sha": "base",
                "head_sha": "head",
                "github_installation_id": installation_id,
            }
        )
        middleware = graph.PrepareReviewScoutRunMiddleware(
            thread_id="scout", config={"configurable": cfg.dump()}
        )
        result = await middleware._prepare({"messages": []}, Runtime())
        assert result["scout_nothing_to_plan"] is True
        sandbox.assert_not_awaited()
        assert await graph._ensure_scout_sandbox("scout", cfg) is sandbox.return_value
        assert (
            thread_token.get_github_token({"configurable": {"thread_id": "scout"}}) == "scout-token"
        )
        assert mint.await_count == 2
        mint.assert_awaited_with(installation_id=installation_id, repositories=["widgets"])
        await thread_token.invalidate_cached_github_token("scout")
