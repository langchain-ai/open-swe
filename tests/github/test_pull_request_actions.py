from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx2
import pytest
from fastapi import HTTPException

from agent.github import pull_request_actions as actions
from agent.github import pull_request_dashboard_routes as pr_routes
from agent.github.tools import approve_pull_request as approve_tool
from agent.run_config import RunConfig


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


@pytest.mark.parametrize("status,state", [(200, "closed"), (200, "open"), (403, "open")])
async def test_close_requires_github_confirmation(github, status, state):
    request = github(
        AsyncMock(return_value=response({"state": state, "message": "Not permitted"}, status))
    )
    action = actions.CloseAction(action="close")
    if status == 200 and state == "closed":
        result = await actions.act_on_pull_request("acme", "app", 7, action, "user-token")
        assert result == actions.PullRequestActionResult(action="close", done=True)
    else:
        with pytest.raises(HTTPException, match="Not permitted"):
            await actions.act_on_pull_request("acme", "app", 7, action, "user-token")
    assert request.await_args.args[1:] == ("PATCH", "https://api.github.com/repos/acme/app/pulls/7")
    assert request.await_args.kwargs == {"json": {"state": "closed"}, "max_retries": 0}


async def test_a_network_failure_is_a_bad_gateway(github):
    github(AsyncMock(side_effect=httpx2.ConnectError("boom")))
    with pytest.raises(HTTPException, match="Could not confirm close") as error:
        await actions.act_on_pull_request(
            "acme", "app", 7, actions.CloseAction(action="close"), "user-token"
        )
    assert error.value.status_code == 502


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


async def test_a_pull_request_already_out_of_draft_is_ready_without_a_mutation(github):
    request = github(AsyncMock(return_value=response({"node_id": "PR_node_7", "draft": False})))
    assert await actions.act_on_pull_request(
        "acme", "app", 7, actions.MarkReadyAction(action="mark-ready"), "user-token"
    ) == actions.PullRequestActionResult(action="mark-ready", done=True)
    assert request.await_count == 1


async def test_approval_reads_and_pins_the_current_head(github, monkeypatch):
    monkeypatch.setattr(actions, "GITHUB_API_BASE", "https://fake-gh")
    sha = "a" * 40
    request = github(
        AsyncMock(
            side_effect=[
                response({"state": "open", "draft": False, "head": {"sha": sha}}),
                response({"state": "APPROVED", "commit_id": sha}, 201),
            ]
        )
    )
    result = await actions.act_on_pull_request(
        "acme", "app", 7, actions.ApproveAction(action="approve", sha=sha), "user-token"
    )
    assert result == actions.PullRequestActionResult(action="approve", done=True)
    assert [call.args[1:] for call in request.await_args_list] == [
        ("GET", "https://fake-gh/repos/acme/app/pulls/7"),
        ("POST", "https://fake-gh/repos/acme/app/pulls/7/reviews"),
    ]
    assert request.await_args_list[0].kwargs == {"max_retries": 0}
    assert request.await_args_list[1].kwargs == {
        "json": {
            "commit_id": sha,
            "event": "APPROVE",
            "body": "Approved via Open SWE review chat.",
        },
        "max_retries": 0,
    }


@pytest.mark.parametrize(
    "pull,message",
    [
        ({"state": "closed", "draft": False, "head": {"sha": "a" * 40}}, "not open"),
        ({"state": "open", "draft": True, "head": {"sha": "a" * 40}}, "still a draft"),
        ({"state": "open", "draft": False, "head": {"sha": "b" * 40}}, "head changed"),
    ],
)
async def test_approval_rejects_an_ineligible_or_changed_pull_request(github, pull, message):
    request = github(AsyncMock(return_value=response(pull)))
    with pytest.raises(HTTPException, match=message):
        await actions.act_on_pull_request(
            "acme",
            "app",
            7,
            actions.ApproveAction(action="approve", sha="a" * 40),
            "user-token",
        )
    assert request.await_count == 1


@pytest.mark.parametrize(
    "payload,status,message",
    [
        ({"message": "Reviews may not approve their own pull request"}, 422, "own pull request"),
        ({"state": "PENDING", "commit_id": "a" * 40}, 201, "did not confirm"),
        ({"state": "APPROVED", "commit_id": "b" * 40}, 201, "did not confirm"),
    ],
)
async def test_approval_requires_github_confirmation(github, payload, status, message):
    sha = "a" * 40
    request = github(
        AsyncMock(
            side_effect=[
                response({"state": "open", "draft": False, "head": {"sha": sha}}),
                response(payload, status),
            ]
        )
    )
    with pytest.raises(HTTPException, match=message) as error:
        await actions.act_on_pull_request(
            "acme", "app", 7, actions.ApproveAction(action="approve", sha=sha), "user-token"
        )
    assert error.value.status_code == (status if 400 <= status < 500 else 502)
    assert request.await_count == 2


async def test_a_refused_mutation_surfaces_githubs_own_message(github):
    github(
        AsyncMock(
            side_effect=[
                response({"node_id": "PR_node_7", "draft": True}),
                response({"errors": [{"message": "Resource not accessible by integration"}]}),
            ]
        )
    )
    with pytest.raises(HTTPException, match="Resource not accessible by integration"):
        await actions.act_on_pull_request(
            "acme", "app", 7, actions.MarkReadyAction(action="mark-ready"), "user-token"
        )


async def test_an_unreadable_pull_request_never_reaches_the_mutation(github):
    request = github(AsyncMock(return_value=response({"message": "Not Found"}, 404)))
    with pytest.raises(HTTPException, match="Not Found") as error:
        await actions.act_on_pull_request(
            "acme", "app", 7, actions.MarkReadyAction(action="mark-ready"), "user-token"
        )
    assert error.value.status_code == 404
    assert request.await_count == 1


@pytest.mark.parametrize(
    "action",
    [
        actions.MergeAction(action="merge", sha="a" * 40, merge_method="squash"),
        actions.CloseAction(action="close"),
        actions.MarkReadyAction(action="mark-ready"),
        actions.ApproveAction(action="approve", sha="a" * 40),
    ],
)
async def test_every_action_rejects_an_invalid_pull_request(action):
    with pytest.raises(HTTPException) as error:
        await actions.act_on_pull_request("acme", "app", 0, action, "user-token")
    assert error.value.status_code == 422


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


async def test_the_route_forwards_the_action_with_the_signed_in_users_token(monkeypatch):
    monkeypatch.setattr(pr_routes, "get_valid_access_token", AsyncMock(return_value="user-token"))
    action = actions.MarkReadyAction(action="mark-ready")
    act = AsyncMock(return_value=actions.PullRequestActionResult(action="mark-ready", done=True))
    monkeypatch.setattr(pr_routes, "act_on_pull_request", act)
    assert await pr_routes.api_act_on_pull_request(
        "acme", "app", 7, action, {"sub": "octocat"}
    ) == actions.PullRequestActionResult(action="mark-ready", done=True)
    act.assert_awaited_once_with("acme", "app", 7, action, "user-token")


async def test_approval_tool_uses_the_authenticated_requesters_token(monkeypatch):
    monkeypatch.setattr(
        approve_tool.RunConfig,
        "from_runtime",
        lambda: RunConfig(source="dashboard"),
    )
    monkeypatch.setattr(approve_tool, "pr_author_login", AsyncMock(return_value="octocat"))
    monkeypatch.setattr(
        approve_tool, "get_valid_access_token", AsyncMock(return_value="user-token")
    )
    act = AsyncMock(return_value=actions.PullRequestActionResult(action="approve", done=True))
    monkeypatch.setattr(approve_tool, "act_on_pull_request", act)
    sha = "a" * 40
    assert await approve_tool.approve_pull_request("acme", "app", 7, sha, True) == {
        "success": True,
        "action": "approve",
    }
    act.assert_awaited_once_with(
        "acme",
        "app",
        7,
        actions.ApproveAction(action="approve", sha=sha),
        "user-token",
    )


async def test_approval_tool_requires_confirmation_before_authorization(monkeypatch):
    authorize = AsyncMock()
    monkeypatch.setattr(approve_tool, "pr_author_login", authorize)
    assert await approve_tool.approve_pull_request("acme", "app", 7, "a" * 40, False) == {
        "success": False,
        "error": "Approval requires explicit confirmation.",
    }
    authorize.assert_not_awaited()


@pytest.mark.parametrize(
    "config",
    [
        RunConfig(source="automation"),
        RunConfig(source="dashboard", background_task_completion=True),
        RunConfig(source="slack", schedule_id="schedule"),
        RunConfig(source="dashboard", watch_key="watch"),
    ],
)
async def test_approval_tool_rejects_noninteractive_runs(monkeypatch, config):
    monkeypatch.setattr(approve_tool.RunConfig, "from_runtime", lambda: config)
    authorize = AsyncMock()
    monkeypatch.setattr(approve_tool, "pr_author_login", authorize)
    assert await approve_tool.approve_pull_request("acme", "app", 7, "a" * 40, True) == {
        "success": False,
        "error": "Approval requires a direct user request.",
    }
    authorize.assert_not_awaited()
