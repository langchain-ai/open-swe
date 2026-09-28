from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import HTTPException

from agent.github import pull_request_actions as actions
from agent.github import pull_request_dashboard_routes as pr_routes


@asynccontextmanager
async def client(**kwargs):
    assert kwargs == {"token": "user-token"}
    yield object()


def response(payload, status=200):
    return httpx2.Response(
        status, json=payload, request=httpx2.Request("GET", "https://api.github.com")
    )


@pytest.fixture
def github(monkeypatch):
    monkeypatch.setattr(actions, "github_client", client)

    def _install(request: AsyncMock) -> AsyncMock:
        monkeypatch.setattr(actions, "github_request", request)
        return request

    return _install


@pytest.mark.parametrize("status,merged", [(200, True), (200, False), (409, False), (403, False)])
async def test_merge_requires_github_confirmation(github, status, merged):
    request = github(
        AsyncMock(return_value=response({"merged": merged, "message": "Head changed"}, status))
    )
    action = actions.MergeAction(action="merge", sha="a" * 40, merge_method="squash")
    if status == 200 and merged:
        result = await actions.act_on_pull_request("acme", "app", 1, action, "user-token")
        assert result == actions.PullRequestActionResult(action="merge", done=True)
    else:
        with pytest.raises(HTTPException, match="Head changed") as error:
            await actions.act_on_pull_request("acme", "app", 1, action, "user-token")
        assert error.value.status_code == (status if 400 <= status < 500 else 502)
    assert request.await_args.args[1:] == (
        "PUT",
        "https://api.github.com/repos/acme/app/pulls/1/merge",
    )
    assert request.await_args.kwargs == {
        "json": {"sha": "a" * 40, "merge_method": "squash"},
        "max_retries": 0,
    }


async def test_retrying_a_close_does_not_post_the_same_reason_twice(github):
    request = github(
        AsyncMock(
            side_effect=[
                response({"comments": 101}),
                response([{"body": "Stale"}]),
                response({"state": "closed"}),
            ]
        )
    )
    action = actions.CloseAction(action="close", reason="Stale")

    await actions.act_on_pull_request("acme", "app", 7, action, "user-token")

    assert [call.args[1:] for call in request.await_args_list] == [
        ("GET", "https://api.github.com/repos/acme/app/issues/7"),
        ("GET", "https://api.github.com/repos/acme/app/issues/7/comments?per_page=100&page=2"),
        ("PATCH", "https://api.github.com/repos/acme/app/pulls/7"),
    ]


async def test_a_refused_reason_comment_leaves_the_pull_request_open(github):
    request = github(
        AsyncMock(side_effect=[response({"comments": 0}), response({"message": "Locked"}, 403)])
    )
    action = actions.CloseAction(action="close", reason="Stale")

    with pytest.raises(HTTPException, match="Locked"):
        await actions.act_on_pull_request("acme", "app", 7, action, "user-token")

    assert request.await_count == 2


async def test_marking_ready_reads_the_node_id_over_rest_then_confirms_the_mutation(
    github, monkeypatch
):
    monkeypatch.setattr(actions, "GITHUB_API_BASE", "https://fake-gh")
    monkeypatch.setattr(actions, "GITHUB_GRAPHQL", "https://fake-gh/graphql")
    request = github(
        AsyncMock(
            side_effect=[
                response({"node_id": "PR_node_7", "draft": True}),
                response(
                    {"data": {"markPullRequestReadyForReview": {"pullRequest": {"isDraft": False}}}}
                ),
            ]
        )
    )
    result = await actions.act_on_pull_request(
        "acme", "app", 7, actions.MarkReadyAction(action="mark-ready"), "user-token"
    )
    assert result == actions.PullRequestActionResult(action="mark-ready", done=True)
    assert [call.args[1:] for call in request.await_args_list] == [
        ("GET", "https://fake-gh/repos/acme/app/pulls/7"),
        ("POST", "https://fake-gh/graphql"),
    ]
    assert request.await_args_list[0].kwargs == {"max_retries": 0}
    assert request.await_args_list[1].kwargs["json"]["variables"] == {"pullRequestId": "PR_node_7"}


async def test_an_unreadable_pull_request_never_reaches_the_mutation(github):
    request = github(AsyncMock(return_value=response({"message": "Not Found"}, 404)))
    with pytest.raises(HTTPException, match="Not Found") as error:
        await actions.act_on_pull_request(
            "acme", "app", 7, actions.MarkReadyAction(action="mark-ready"), "user-token"
        )
    assert error.value.status_code == 404
    assert request.await_count == 1


async def test_without_a_user_token_the_route_never_calls_github(monkeypatch):
    monkeypatch.setattr(pr_routes, "get_valid_access_token", AsyncMock(return_value=None))
    act = AsyncMock()
    monkeypatch.setattr(pr_routes, "act_on_pull_request", act)
    with pytest.raises(HTTPException) as error:
        await pr_routes.api_act_on_pull_request(
            "acme", "app", 7, actions.CloseAction(action="close"), {"sub": "octocat"}
        )
    assert error.value.status_code == 401
    act.assert_not_awaited()


@pytest.mark.parametrize("status", [202, 422])
async def test_updating_the_branch_pins_the_head_the_viewer_saw(github, status):
    request = github(
        AsyncMock(return_value=response({"message": "expected head sha didn't match"}, status))
    )
    action = actions.UpdateBranchAction(action="update-branch", sha="b" * 40)
    if status == 202:
        result = await actions.act_on_pull_request("acme", "app", 7, action, "user-token")
        assert result == actions.PullRequestActionResult(action="update-branch", done=True)
    else:
        with pytest.raises(HTTPException, match="expected head sha") as error:
            await actions.act_on_pull_request("acme", "app", 7, action, "user-token")
        assert error.value.status_code == 422
    assert request.await_args.args[1:] == (
        "PUT",
        "https://api.github.com/repos/acme/app/pulls/7/update-branch",
    )
    assert request.await_args.kwargs == {
        "json": {"expected_head_sha": "b" * 40},
        "max_retries": 0,
    }
