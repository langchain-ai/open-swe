from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from agent.middleware.verify_push_claims import VerifyPushClaimsMiddleware


class _Request:
    def __init__(
        self,
        *,
        tool_call: dict[str, Any],
        messages: list[Any] | None = None,
    ) -> None:
        self.tool_call = tool_call
        self.state = {"messages": messages or []}
        self.runtime = None

    def override(self, **kwargs: Any) -> _Request:  # pragma: no cover - unused
        next_request = _Request(tool_call=self.tool_call, messages=self.state["messages"])
        for k, v in kwargs.items():
            setattr(next_request, k, v)
        return next_request


def _commit_only_history() -> list[Any]:
    return [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "execute",
                    "args": {"command": "git -C /repo commit -am 'fix'"},
                    "id": "c1",
                }
            ],
        ),
    ]


def _history_with_push() -> list[Any]:
    return [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "execute",
                    "args": {"command": "git -C /repo commit -am 'fix'"},
                    "id": "c1",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "execute",
                    "args": {"command": "git -C /repo push origin feature"},
                    "id": "p1",
                }
            ],
        ),
    ]


async def test_blocks_slack_reply_claiming_push_without_evidence() -> None:
    request = _Request(
        tool_call={
            "name": "slack_thread_reply",
            "args": {"message": "Pushed commit abc123 to PR #42"},
            "id": "call-1",
        },
        messages=_commit_only_history(),
    )

    async def handler(_request: Any) -> ToolMessage:
        raise AssertionError("handler should not run when reply is blocked")

    result = await VerifyPushClaimsMiddleware().awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call-1"
    assert "no `git push`" in str(result.content)


async def test_allows_slack_reply_when_recent_push_observed() -> None:
    request = _Request(
        tool_call={
            "name": "slack_thread_reply",
            "args": {"message": "Pushed commit abc123 to PR #42"},
            "id": "call-2",
        },
        messages=_history_with_push(),
    )

    called = False

    async def handler(_request: Any) -> ToolMessage:
        nonlocal called
        called = True
        return ToolMessage(content="ok", tool_call_id="call-2")

    result = await VerifyPushClaimsMiddleware().awrap_tool_call(request, handler)

    assert called is True
    assert isinstance(result, ToolMessage)
    assert result.content == "ok"


async def test_allows_slack_reply_when_open_pull_request_observed() -> None:
    history = [
        AIMessage(
            content="",
            tool_calls=[{"name": "open_pull_request", "args": {}, "id": "pr1"}],
        ),
    ]
    request = _Request(
        tool_call={
            "name": "slack_thread_reply",
            "args": {"message": "Pushed the branch and opened a PR."},
            "id": "call-3",
        },
        messages=history,
    )

    async def handler(_request: Any) -> ToolMessage:
        return ToolMessage(content="ok", tool_call_id="call-3")

    result = await VerifyPushClaimsMiddleware().awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    assert result.content == "ok"


async def test_allows_slack_reply_with_no_push_claim() -> None:
    request = _Request(
        tool_call={
            "name": "slack_thread_reply",
            "args": {"message": "Investigating the failing test now."},
            "id": "call-4",
        },
        messages=_commit_only_history(),
    )

    async def handler(_request: Any) -> ToolMessage:
        return ToolMessage(content="ok", tool_call_id="call-4")

    result = await VerifyPushClaimsMiddleware().awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    assert result.content == "ok"


async def test_blocks_linear_comment_claiming_push() -> None:
    request = _Request(
        tool_call={
            "name": "linear_comment",
            "args": {
                "comment_body": "Pushing to feature-branch now.",
                "ticket_id": "ENG-1",
            },
            "id": "call-5",
        },
        messages=_commit_only_history(),
    )

    async def handler(_request: Any) -> ToolMessage:
        raise AssertionError("handler should not run")

    result = await VerifyPushClaimsMiddleware().awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"


async def test_blocks_gh_pr_comment_claiming_push() -> None:
    request = _Request(
        tool_call={
            "name": "execute",
            "args": {
                "command": "gh pr comment 42 --body 'Force-pushed the fixup commit.'",
            },
            "id": "call-6",
        },
        messages=_commit_only_history(),
    )

    async def handler(_request: Any) -> ToolMessage:
        raise AssertionError("handler should not run")

    result = await VerifyPushClaimsMiddleware().awrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"


async def test_ignores_unrelated_execute_command() -> None:
    request = _Request(
        tool_call={
            "name": "execute",
            "args": {"command": "pytest -q"},
            "id": "call-7",
        },
        messages=_commit_only_history(),
    )

    called = False

    async def handler(_request: Any) -> ToolMessage:
        nonlocal called
        called = True
        return ToolMessage(content="ok", tool_call_id="call-7")

    result = await VerifyPushClaimsMiddleware().awrap_tool_call(request, handler)
    assert called is True
    assert isinstance(result, ToolMessage)
    assert result.content == "ok"


def test_sync_wrap_tool_call_blocks_push_claim() -> None:
    request = _Request(
        tool_call={
            "name": "slack_thread_reply",
            "args": {"message": "Pushed commit abc123."},
            "id": "call-8",
        },
        messages=_commit_only_history(),
    )

    def handler(_request: Any) -> ToolMessage:
        raise AssertionError("handler should not run")

    result = VerifyPushClaimsMiddleware().wrap_tool_call(request, handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
