import json
from unittest.mock import AsyncMock, patch

import pytest
from langsmith.sandbox import (
    SandboxConnectionError,
    SandboxOperationError,
    SandboxRetryableConnectionError,
)

from openswe.agent_api.failures import OperationFailure

_CONFIG = {"configurable": {"thread_id": "thread-1"}}


async def test_unreachable_sandbox_notifies_then_ends_the_run() -> None:
    """A dead sandbox tells the user once, then ends the run; it is never swapped out.

    Replacing it mid-run gives the agent an empty filesystem while it still
    believes its working tree is intact, silently destroying uncommitted work.
    Handing the model an error message instead would leave every later sandbox
    call hitting the same dead backend and notifying the user again.
    """
    with patch(
        "openswe.agent_api.failures.post_sandbox_unreachable_notification",
        new_callable=AsyncMock,
    ) as notify:
        failure = await OperationFailure.of(
            SandboxConnectionError("WebSocket upgrade to sb-dead failed (no valid HTTP response)"),
            operation="background_execute",
            config=_CONFIG,
        )

    assert failure.sandbox_unreachable
    notify.assert_awaited_once()


async def test_transient_sandbox_error_keeps_the_sandbox_and_asks_for_a_retry() -> None:
    """A rejected WebSocket upgrade is a gateway blip, not a dead sandbox.

    The command never started, so the user is not told their sandbox went quiet:
    the model is told to retry the call.
    """
    with patch(
        "openswe.agent_api.failures.post_sandbox_unreachable_notification",
        new_callable=AsyncMock,
    ) as notify:
        failure = await OperationFailure.of(
            SandboxRetryableConnectionError(
                "WebSocket upgrade temporarily rejected by server (HTTP 503): "
                "server rejected WebSocket connection: HTTP 503"
            ),
            operation="background_execute",
            config=_CONFIG,
        )

    notify.assert_not_awaited()
    assert not failure.sandbox_unreachable
    payload = json.loads(failure.content)
    assert payload["recovery"] == "sandbox_transient"
    assert payload["error_type"] == "SandboxRetryableConnectionError"
    assert "nothing ran and nothing changed" in payload["error"]


async def test_command_level_sandbox_error_does_not_end_the_run() -> None:
    """A failed command says nothing about whether the sandbox is reachable.

    ``SandboxOperationError`` covers command error frames, such as a missing binary
    or an expired session, so ending the run and telling the user their sandbox died
    would be both wrong and unrecoverable.
    """
    with patch(
        "openswe.agent_api.failures.post_sandbox_unreachable_notification",
        new_callable=AsyncMock,
    ) as notify:
        failure = await OperationFailure.of(
            SandboxOperationError("CommandNotFound: no such file or directory: fzf"),
            operation="background_execute",
            config=_CONFIG,
        )

    notify.assert_not_awaited()
    assert not failure.sandbox_unreachable
    payload = json.loads(failure.content)
    assert payload["status"] == "error"
    assert payload["error_type"] == "SandboxOperationError"
    assert "recovery" not in payload


@pytest.mark.parametrize(
    ("kind", "posted"), [("reviewer", False), ("review-scout", False), (None, True)]
)
async def test_a_review_leaves_its_lost_sandbox_to_its_own_failure_notice(
    monkeypatch: pytest.MonkeyPatch, kind: str | None, posted: bool
) -> None:
    """A review's failure notice and check already tell the PR; a second warning repeats it."""
    from openswe.middleware import sandbox_circuit_breaker

    monkeypatch.setattr(
        sandbox_circuit_breaker, "get_active_slack_thread", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        sandbox_circuit_breaker,
        "read_thread",
        AsyncMock(return_value={"metadata": {"kind": kind} if kind else {}}),
    )
    monkeypatch.setattr(
        sandbox_circuit_breaker, "resolve_thread_github_token", AsyncMock(return_value="token")
    )
    comment = AsyncMock()
    monkeypatch.setattr(sandbox_circuit_breaker, "post_github_comment", comment)

    await sandbox_circuit_breaker.post_sandbox_unreachable_notification(
        {
            "configurable": {
                "thread_id": "thread-1",
                "repo": {"owner": "acme", "name": "api"},
                "pr_number": 7,
            }
        },
        sandbox_id="sb-dead",
    )

    assert comment.await_count == (1 if posted else 0)
