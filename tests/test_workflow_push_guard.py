from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from agent.middleware import workflow_push_guard as guard


class _Response:
    def __init__(self, output: str, exit_code: int = 0) -> None:
        self.output = output
        self.exit_code = exit_code
        self.truncated = False


class _Backend:
    id = "sandbox-id"

    def __init__(self, *, workflow_files: str = ".github/workflows/ci.yml") -> None:
        self.workflow_files = workflow_files
        self.commands: list[str] = []
        self.head = "head-sha"

    def execute(self, command: str, *, timeout: int | None = None) -> _Response:
        self.commands.append(command)
        if "rev-parse --show-toplevel" in command:
            return _Response("/repo\n")
        if "rev-parse --abbrev-ref --symbolic-full-name @{u}" in command:
            return _Response("", 1)
        if "symbolic-ref --short refs/remotes/origin/HEAD" in command:
            return _Response("origin/main\n")
        if "merge-base HEAD origin/main" in command:
            return _Response("base-sha\n")
        if "diff --name-only" in command:
            return _Response(f"{self.workflow_files}\n" if self.workflow_files else "")
        if "diff --binary --full-index" in command:
            return _Response("diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n")
        if "config --get remote.origin.url" in command:
            return _Response("git@github.com:langchain-ai/open-swe.git\n")
        if "rev-parse --abbrev-ref HEAD" in command:
            return _Response("feature\n")
        if "rev-parse HEAD" in command:
            return _Response(f"{self.head}\n")
        return _Response("")


class _Runtime:
    config = {
        "configurable": {
            "thread_id": "thread-1",
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"},
        }
    }


class _Request:
    runtime = _Runtime()

    def __init__(self, command: str = "git -C /repo push origin feature") -> None:
        self.tool_call = {
            "name": "execute",
            "args": {"command": command},
            "id": "call-1",
        }


@pytest.fixture(autouse=True)
def _clear_backend_cache() -> Any:
    guard.SANDBOX_BACKENDS.clear()
    yield
    guard.SANDBOX_BACKENDS.clear()


def test_parse_git_push_supports_git_c_and_cd() -> None:
    assert guard._parse_git_push("git -C /repo push origin feature") == guard.ParsedGitPush(
        repo_dir="/repo"
    )
    assert guard._parse_git_push("cd /repo && git push origin feature") == guard.ParsedGitPush(
        repo_dir="/repo"
    )
    assert guard._parse_git_push("git status && git push") is None


def test_workflow_change_for_push_fingerprints_workflow_diff() -> None:
    backend = _Backend()
    change = guard._workflow_change_for_push(backend, guard.ParsedGitPush(repo_dir="/repo"))

    assert change is not None
    assert change.repo == "https://github.com/langchain-ai/open-swe"
    assert change.branch == "feature"
    assert change.files == [".github/workflows/ci.yml"]
    assert len(change.fingerprint) == 64


def test_workflow_change_for_push_ignores_non_workflow_push() -> None:
    backend = _Backend(workflow_files="")

    assert guard._workflow_change_for_push(backend, guard.ParsedGitPush(repo_dir="/repo")) is None


async def test_unapproved_workflow_push_blocks_and_posts_slack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard.SANDBOX_BACKENDS["thread-1"] = _Backend()
    posted: dict[str, Any] = {}

    async def fake_approved(thread_id: str, fingerprint: str) -> bool:
        return False

    async def fake_pending(thread_id: str, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        return {"fingerprint": kwargs["fingerprint"], "status": "pending", "notified": False}, True

    async def fake_post(
        channel_id: str, thread_ts: str, message: str, **kwargs: Any
    ) -> tuple[str, None]:
        posted.update(
            channel_id=channel_id, thread_ts=thread_ts, message=message, blocks=kwargs["blocks"]
        )
        return "1700000000.000200", None

    async def fake_notified(thread_id: str, fingerprint: str) -> None:
        posted["notified"] = fingerprint

    monkeypatch.setattr(guard, "workflow_push_approved", fake_approved)
    monkeypatch.setattr(guard, "ensure_workflow_push_pending", fake_pending)
    monkeypatch.setattr(guard, "post_slack_thread_reply_with_ts", fake_post)
    monkeypatch.setattr(guard, "mark_workflow_push_notified", fake_notified)

    called = False

    async def handler(_request: Any) -> ToolMessage:
        nonlocal called
        called = True
        return ToolMessage(content="pushed", tool_call_id="call-1")

    result = await guard.WorkflowPushGuardMiddleware().awrap_tool_call(_Request(), handler)

    assert called is False
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    payload = json.loads(str(result.content))
    assert payload["workflow_approval_status"] == "approval_required"
    assert payload["files"] == [".github/workflows/ci.yml"]
    assert posted["channel_id"] == "C123"
    assert posted["blocks"][1]["elements"][0]["value"]


async def test_approved_workflow_push_elevates_and_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard.SANDBOX_BACKENDS["thread-1"] = _Backend()
    refreshed: list[dict[str, str]] = []

    async def fake_approved(thread_id: str, fingerprint: str) -> bool:
        return True

    async def fake_refresh(thread_id: str | None, *, permissions: dict[str, str]) -> bool:
        refreshed.append(dict(permissions))
        return True

    monkeypatch.setattr(guard, "workflow_push_approved", fake_approved)
    monkeypatch.setattr(guard, "refresh_proxy_token", fake_refresh)

    async def handler(_request: Any) -> ToolMessage:
        return ToolMessage(content="pushed", tool_call_id="call-1")

    result = await guard.WorkflowPushGuardMiddleware().awrap_tool_call(_Request(), handler)

    assert isinstance(result, ToolMessage)
    assert result.content == "pushed"
    assert refreshed[0]["workflows"] == "write"
    assert "workflows" not in refreshed[1]


async def test_non_workflow_push_runs_without_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    guard.SANDBOX_BACKENDS["thread-1"] = _Backend(workflow_files="")
    called = False

    async def fail_approval(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("approval should not be checked")

    monkeypatch.setattr(guard, "workflow_push_approved", fail_approval)

    async def handler(_request: Any) -> ToolMessage:
        nonlocal called
        called = True
        return ToolMessage(content="pushed", tool_call_id="call-1")

    result = await guard.WorkflowPushGuardMiddleware().awrap_tool_call(_Request(), handler)

    assert called is True
    assert isinstance(result, ToolMessage)
    assert result.content == "pushed"
