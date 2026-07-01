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
        self.head = "a" * 40

    def execute(self, command: str, *, timeout: int | None = None) -> _Response:
        self.commands.append(command)
        if "rev-parse --show-toplevel" in command:
            return _Response("/repo\n")
        if "rev-parse --verify refs/remotes/origin/feature" in command:
            return _Response("", 1)
        if "symbolic-ref --short refs/remotes/origin/HEAD" in command:
            return _Response("origin/main\n")
        if f"merge-base {self.head} origin/main" in command:
            return _Response("base-sha\n")
        if "diff --name-only" in command:
            return _Response(f"{self.workflow_files}\n" if self.workflow_files else "")
        if "diff --binary --full-index" in command:
            return _Response(
                "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml\n+new\n-old\n"
            )
        if "diff --numstat" in command:
            return _Response("1\t1\t.github/workflows/ci.yml\n")
        if "config --get remote.origin.url" in command:
            return _Response("git@github.com:langchain-ai/open-swe.git\n")
        if "rev-parse --abbrev-ref HEAD" in command:
            return _Response("feature\n")
        if "rev-parse HEAD" in command or "rev-parse feature" in command:
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

    def override(self, **kwargs: Any) -> _Request:
        next_request = _Request()
        next_request.tool_call = kwargs.get("tool_call", self.tool_call)
        return next_request


@pytest.fixture(autouse=True)
def _clear_backend_cache() -> Any:
    guard.SANDBOX_BACKENDS.clear()
    yield
    guard.SANDBOX_BACKENDS.clear()


def test_parse_git_push_supports_git_c_and_cd() -> None:
    assert guard._parse_git_push("git -C /repo push origin feature") == guard.ParsedGitPush(
        repo_dir="/repo", remote="origin", local_ref="feature", remote_ref="feature"
    )
    assert guard._parse_git_push(
        "cd /repo && git push -u origin HEAD:feature"
    ) == guard.ParsedGitPush(
        repo_dir="/repo",
        remote="origin",
        local_ref="HEAD",
        remote_ref="feature",
        set_upstream=True,
    )
    assert guard._parse_git_push("git status && git push") is None
    assert guard._parse_git_push("git push origin feature; git push origin evil:feature") is None


def test_workflow_change_for_push_fingerprints_workflow_diff() -> None:
    backend = _Backend()
    change = guard._workflow_change_for_push(
        backend,
        guard.ParsedGitPush(
            repo_dir="/repo", remote="origin", local_ref="feature", remote_ref="feature"
        ),
    )

    assert change is not None
    assert change.repo == "https://github.com/langchain-ai/open-swe"
    assert change.branch == "feature"
    assert change.files == [".github/workflows/ci.yml"]
    assert change.diff_stats == {"files": 1, "additions": 1, "deletions": 1}
    assert change.diff_preview_truncated is False
    assert "diff --git" in change.diff_preview
    assert (
        change.fixed_command
        == "git -C /repo push origin aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:refs/heads/feature"
    )
    assert len(change.fingerprint) == 64


def test_workflow_approval_response_serializes_review_fields() -> None:
    from agent.dashboard.workflow_approval import workflow_push_approval_response

    response = workflow_push_approval_response(
        {
            "fingerprint": "abc",
            "status": "pending",
            "repo": "https://github.com/langchain-ai/open-swe",
            "branch": "feature",
            "base_sha": "b" * 40,
            "head_sha": "a" * 40,
            "files": [".github/workflows/ci.yml"],
            "diff_stats": {"files": 1, "additions": 2, "deletions": 3},
            "diff_preview": "diff --git ...",
            "diff_preview_truncated": True,
            "approval_url": "https://openswe.vercel.app/agents/thread?workflowApproval=abc",
            "requested_at": "2026-06-30T00:00:00+00:00",
        }
    )

    assert response["fingerprint"] == "abc"
    assert response["baseSha"] == "b" * 40
    assert response["headSha"] == "a" * 40
    assert response["diffStats"] == {"files": 1, "additions": 2, "deletions": 3}
    assert response["diffPreviewTruncated"] is True
    assert response["approvalUrl"].endswith("workflowApproval=abc")


def test_workflow_change_for_push_ignores_non_workflow_push() -> None:
    backend = _Backend(workflow_files="")

    assert (
        guard._workflow_change_for_push(
            backend,
            guard.ParsedGitPush(
                repo_dir="/repo", remote="origin", local_ref="feature", remote_ref="feature"
            ),
        )
        is None
    )


def test_workflow_change_for_push_rejects_non_current_refspec() -> None:
    backend = _Backend()

    assert (
        guard._workflow_change_for_push(
            backend,
            guard.ParsedGitPush(
                repo_dir="/repo", remote="origin", local_ref="evil", remote_ref="feature"
            ),
        )
        is None
    )


async def test_unapproved_workflow_push_blocks_and_posts_slack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard.SANDBOX_BACKENDS["thread-1"] = _Backend()
    posted: dict[str, Any] = {}

    async def fake_approved(thread_id: str, fingerprint: str) -> bool:
        return False

    pending_kwargs: dict[str, Any] = {}

    async def fake_pending(thread_id: str, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        pending_kwargs.update(kwargs)
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
    assert payload["diff_stats"] == {"files": 1, "additions": 1, "deletions": 1}
    assert payload["approval_url"].endswith("?workflowApproval=" + payload["fingerprint"])
    assert pending_kwargs["diff_preview"]
    assert pending_kwargs["diff_preview_truncated"] is False
    assert pending_kwargs["approval_url"] == payload["approval_url"]
    assert posted["channel_id"] == "C123"
    assert "Open in Web" in posted["message"]
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

    pushed_command = ""

    async def handler(request: Any) -> ToolMessage:
        nonlocal pushed_command
        pushed_command = request.tool_call["args"]["command"]
        return ToolMessage(content="pushed", tool_call_id="call-1")

    result = await guard.WorkflowPushGuardMiddleware().awrap_tool_call(_Request(), handler)

    assert isinstance(result, ToolMessage)
    assert result.content == "pushed"
    assert (
        pushed_command
        == "git -C /repo push origin aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:refs/heads/feature"
    )
    assert refreshed[0]["workflows"] == "write"
    assert "workflows" not in refreshed[1]
    assert refreshed[1]["actions"] == "read"


async def test_workflow_push_restoration_falls_back_when_actions_read_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard.SANDBOX_BACKENDS["thread-1"] = _Backend()
    refreshed: list[dict[str, str]] = []

    async def fake_approved(thread_id: str, fingerprint: str) -> bool:
        return True

    async def fake_refresh(thread_id: str | None, *, permissions: dict[str, str]) -> bool:
        refreshed.append(dict(permissions))
        return "actions" not in permissions

    monkeypatch.setattr(guard, "workflow_push_approved", fake_approved)
    monkeypatch.setattr(guard, "refresh_proxy_token", fake_refresh)

    async def handler(_request: Any) -> ToolMessage:
        return ToolMessage(content="pushed", tool_call_id="call-1")

    await guard.WorkflowPushGuardMiddleware().awrap_tool_call(_Request(), handler)

    assert refreshed[0]["workflows"] == "write"
    assert refreshed[1]["actions"] == "read"
    assert refreshed[2] == guard.BASE_RUNTIME_PROXY_TOKEN_PERMISSIONS
    assert "actions" not in refreshed[2]


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
