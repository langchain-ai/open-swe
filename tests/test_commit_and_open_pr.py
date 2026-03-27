"""Tests for the commit_and_open_pr tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from deepagents.backends.protocol import ExecuteResponse

from agent.tools.commit_and_open_pr import commit_and_open_pr


class _FakeSandboxBackend:
    """Minimal sandbox backend that records executed commands."""

    def __init__(
        self,
        *,
        has_uncommitted: bool = True,
        has_unpushed: bool = False,
        current_branch: str = "main",
        commit_ok: bool = True,
        push_ok: bool = True,
    ) -> None:
        self._has_uncommitted = has_uncommitted
        self._has_unpushed = has_unpushed
        self._current_branch = current_branch
        self._commit_ok = commit_ok
        self._push_ok = push_ok
        self.commands: list[str] = []
        self.written_files: dict[str, str] = {}

    def execute(self, command: str) -> ExecuteResponse:
        self.commands.append(command)

        if "git status --porcelain" in command:
            output = "M file.py\n" if self._has_uncommitted else ""
            return ExecuteResponse(output=output, exit_code=0, truncated=False)

        if "git fetch origin" in command:
            return ExecuteResponse(output="", exit_code=0, truncated=False)

        if "git log --oneline" in command:
            output = "abc1234 some commit\n" if self._has_unpushed else ""
            return ExecuteResponse(output=output, exit_code=0, truncated=False)

        if "git rev-parse --abbrev-ref HEAD" in command:
            return ExecuteResponse(output=self._current_branch + "\n", exit_code=0, truncated=False)

        if "git checkout" in command:
            return ExecuteResponse(output="", exit_code=0, truncated=False)

        if "git config user.name" in command or "git config user.email" in command:
            return ExecuteResponse(output="", exit_code=0, truncated=False)

        if "git add -A" in command:
            return ExecuteResponse(output="", exit_code=0, truncated=False)

        if "git commit -m" in command:
            code = 0 if self._commit_ok else 1
            output = "" if self._commit_ok else "nothing to commit"
            return ExecuteResponse(output=output, exit_code=code, truncated=False)

        if "git push" in command or "credential.helper" in command:
            code = 0 if self._push_ok else 1
            output = "" if self._push_ok else "rejected"
            return ExecuteResponse(output=output, exit_code=code, truncated=False)

        if "rm -f" in command or "chmod" in command:
            return ExecuteResponse(output="", exit_code=0, truncated=False)

        if "test -d" in command:
            return ExecuteResponse(output="/workspace", exit_code=0, truncated=False)

        if command.strip() == "pwd":
            return ExecuteResponse(output="/workspace\n", exit_code=0, truncated=False)

        return ExecuteResponse(output="", exit_code=0, truncated=False)

    def write(self, path: str, content: str) -> None:
        self.written_files[path] = content


def _make_config(
    thread_id: str = "test-thread-id",
    repo_owner: str = "test-owner",
    repo_name: str = "test-repo",
) -> dict[str, Any]:
    return {
        "configurable": {
            "thread_id": thread_id,
            "repo": {"owner": repo_owner, "name": repo_name},
        },
        "metadata": {},
    }


def _run_commit_tool(
    sandbox: _FakeSandboxBackend,
    config: dict[str, Any] | None = None,
    title: str = "feat: test PR",
    body: str = "## Description\nTest PR body",
    commit_message: str | None = None,
    github_token: str = "fake-token",
    default_branch: str = "main",
    pr_url: str = "https://github.com/test-owner/test-repo/pull/42",
    pr_number: int = 42,
) -> dict[str, Any]:
    """Run commit_and_open_pr with mocked dependencies."""
    if config is None:
        config = _make_config()

    with (
        patch("agent.tools.commit_and_open_pr.get_config", return_value=config),
        patch("agent.tools.commit_and_open_pr.get_sandbox_backend_sync", return_value=sandbox),
        patch("agent.tools.commit_and_open_pr.get_github_token", return_value=github_token),
        patch(
            "agent.tools.commit_and_open_pr.get_github_default_branch",
            new_callable=AsyncMock,
            return_value=default_branch,
        ),
        patch(
            "agent.tools.commit_and_open_pr.create_github_pr",
            new_callable=AsyncMock,
            return_value=(pr_url, pr_number, False),
        ),
    ):
        return commit_and_open_pr(title, body, commit_message)


class TestCommitAndOpenPrHappyPath:
    def test_basic_success(self) -> None:
        sandbox = _FakeSandboxBackend(has_uncommitted=True)
        result = _run_commit_tool(sandbox)

        assert result["success"] is True
        assert result["pr_url"] == "https://github.com/test-owner/test-repo/pull/42"
        assert result["error"] is None

    def test_uses_title_as_default_commit_message(self) -> None:
        sandbox = _FakeSandboxBackend(has_uncommitted=True)
        _run_commit_tool(sandbox, title="feat: add feature X")

        commit_cmds = [c for c in sandbox.commands if "git commit -m" in c]
        assert len(commit_cmds) == 1
        assert "add feature X" in commit_cmds[0]

    def test_uses_custom_commit_message(self) -> None:
        sandbox = _FakeSandboxBackend(has_uncommitted=True)
        _run_commit_tool(sandbox, commit_message="custom msg here")

        commit_cmds = [c for c in sandbox.commands if "git commit -m" in c]
        assert len(commit_cmds) == 1
        assert "custom msg here" in commit_cmds[0]

    def test_checks_out_target_branch(self) -> None:
        sandbox = _FakeSandboxBackend(has_uncommitted=True, current_branch="main")
        _run_commit_tool(sandbox)

        checkout_cmds = [c for c in sandbox.commands if "git checkout" in c]
        assert len(checkout_cmds) >= 1
        assert "open-swe/test-thread-id" in checkout_cmds[0]

    def test_skips_checkout_if_already_on_target_branch(self) -> None:
        sandbox = _FakeSandboxBackend(
            has_uncommitted=True, current_branch="open-swe/test-thread-id"
        )
        _run_commit_tool(sandbox)

        checkout_cmds = [c for c in sandbox.commands if "git checkout" in c]
        assert len(checkout_cmds) == 0

    def test_existing_pr_returns_pr_existing_true(self) -> None:
        sandbox = _FakeSandboxBackend(has_uncommitted=True)
        config = _make_config()

        with (
            patch("agent.tools.commit_and_open_pr.get_config", return_value=config),
            patch(
                "agent.tools.commit_and_open_pr.get_sandbox_backend_sync",
                return_value=sandbox,
            ),
            patch(
                "agent.tools.commit_and_open_pr.get_github_token",
                return_value="fake-token",
            ),
            patch(
                "agent.tools.commit_and_open_pr.get_github_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
            patch(
                "agent.tools.commit_and_open_pr.create_github_pr",
                new_callable=AsyncMock,
                return_value=(
                    "https://github.com/test/pr/99",
                    99,
                    True,
                ),
            ),
        ):
            result = commit_and_open_pr("feat: test", "body")

        assert result["success"] is True
        assert result["pr_existing"] is True

    def test_unpushed_commits_only(self) -> None:
        sandbox = _FakeSandboxBackend(has_uncommitted=False, has_unpushed=True)
        result = _run_commit_tool(sandbox)

        assert result["success"] is True
        commit_cmds = [c for c in sandbox.commands if "git commit -m" in c]
        assert len(commit_cmds) == 0


class TestCommitAndOpenPrErrorCases:
    def test_no_thread_id(self) -> None:
        sandbox = _FakeSandboxBackend()
        config = _make_config()
        config["configurable"]["thread_id"] = None

        result = _run_commit_tool(sandbox, config=config)
        assert result["success"] is False
        assert "thread_id" in result["error"].lower()

    def test_no_repo_config(self) -> None:
        sandbox = _FakeSandboxBackend()
        config = _make_config()
        config["configurable"]["repo"] = {}

        result = _run_commit_tool(sandbox, config=config)
        assert result["success"] is False
        assert "repo" in result["error"].lower()

    def test_no_sandbox(self) -> None:
        config = _make_config()

        with (
            patch("agent.tools.commit_and_open_pr.get_config", return_value=config),
            patch("agent.tools.commit_and_open_pr.get_sandbox_backend_sync", return_value=None),
        ):
            result = commit_and_open_pr("feat: test", "body")

        assert result["success"] is False
        assert "sandbox" in result["error"].lower()

    def test_no_changes(self) -> None:
        sandbox = _FakeSandboxBackend(has_uncommitted=False, has_unpushed=False)
        result = _run_commit_tool(sandbox)

        assert result["success"] is False
        assert "no changes" in result["error"].lower()

    def test_commit_failure(self) -> None:
        sandbox = _FakeSandboxBackend(has_uncommitted=True, commit_ok=False)
        result = _run_commit_tool(sandbox)

        assert result["success"] is False
        assert "commit failed" in result["error"].lower()

    def test_push_failure(self) -> None:
        sandbox = _FakeSandboxBackend(has_uncommitted=True, push_ok=False)
        result = _run_commit_tool(sandbox)

        assert result["success"] is False
        assert "push failed" in result["error"].lower()

    def test_missing_github_token(self) -> None:
        sandbox = _FakeSandboxBackend(has_uncommitted=True)
        result = _run_commit_tool(sandbox, github_token=None)

        assert result["success"] is False
        assert "token" in result["error"].lower()

    def test_pr_creation_failure(self) -> None:
        sandbox = _FakeSandboxBackend(has_uncommitted=True)
        config = _make_config()

        with (
            patch("agent.tools.commit_and_open_pr.get_config", return_value=config),
            patch(
                "agent.tools.commit_and_open_pr.get_sandbox_backend_sync",
                return_value=sandbox,
            ),
            patch(
                "agent.tools.commit_and_open_pr.get_github_token",
                return_value="fake-token",
            ),
            patch(
                "agent.tools.commit_and_open_pr.get_github_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
            patch(
                "agent.tools.commit_and_open_pr.create_github_pr",
                new_callable=AsyncMock,
                return_value=(None, None, False),
            ),
        ):
            result = commit_and_open_pr("feat: test", "body")

        assert result["success"] is False
        assert "pr" in result["error"].lower()

    def test_exception_returns_error_dict(self) -> None:
        config = _make_config()

        with (
            patch("agent.tools.commit_and_open_pr.get_config", return_value=config),
            patch(
                "agent.tools.commit_and_open_pr.get_sandbox_backend_sync",
                side_effect=RuntimeError("boom"),
            ),
        ):
            result = commit_and_open_pr("feat: test", "body")

        assert result["success"] is False
        assert "RuntimeError" in result["error"]
