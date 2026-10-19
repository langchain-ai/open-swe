from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.runtime import Runtime

from openswe.review_scout import graph
from openswe.sandboxes.repo_prep import ReviewRepoPreparationError


@pytest.mark.parametrize(
    ("failures", "heads", "expected"),
    [
        (1, ["head", "head"], "complete"),
        (2, ["head", "head"], "error"),
        (1, ["new"], "stale"),
        (2, ["head", "new"], "stale"),
        (0, ["new"], "stale"),
        (0, ["head"], "moved"),
    ],
)
async def test_scout_preparation_retries_and_checks_head_without_a_pinned_checkout(
    monkeypatch: pytest.MonkeyPatch, failures: int, heads: list[str], expected: str
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
        return_value=MagicMock(complete=True),
    )
    monkeypatch.setattr(graph, "_workspace", workspace)
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
        assert result["scout_nothing_to_plan"] is True
        assert checkout.pin.await_count == (1 if expected in ("complete", "moved") else 0)
        assert workspace.await_count == (1 if expected in ("complete", "moved") else 0)
    assert prepare.await_count == len(heads)
    assert sleep.await_args_list == ([] if len(heads) == 1 else [((3,), {})])
