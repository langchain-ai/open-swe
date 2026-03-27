"""Tests for git utilities in agent.utils.github."""

from __future__ import annotations

from deepagents.backends.protocol import ExecuteResponse

from agent.utils.github import (
    _CRED_FILE_PATH,
    cleanup_git_credentials,
    git_add_all,
    git_checkout_branch,
    git_commit,
    git_config_user,
    git_current_branch,
    git_get_remote_url,
    git_has_uncommitted_changes,
    git_has_unpushed_commits,
    git_push,
    is_valid_git_repo,
    remove_directory,
    setup_git_credentials,
)


class _FakeSandbox:
    """Minimal sandbox backend for testing git utilities."""

    def __init__(self, responses: dict[str, ExecuteResponse] | None = None) -> None:
        self._responses = responses or {}
        self.commands: list[str] = []
        self.written_files: dict[str, str] = {}

    def execute(self, command: str) -> ExecuteResponse:
        self.commands.append(command)
        for pattern, response in self._responses.items():
            if pattern in command:
                return response
        return ExecuteResponse(output="", exit_code=0, truncated=False)

    def write(self, path: str, content: str) -> None:
        self.written_files[path] = content


# ---------------------------------------------------------------------------
# is_valid_git_repo
# ---------------------------------------------------------------------------


class TestIsValidGitRepo:
    def test_valid_repo(self) -> None:
        sandbox = _FakeSandbox(
            {"test -d": ExecuteResponse(output="exists\n", exit_code=0, truncated=False)}
        )
        assert is_valid_git_repo(sandbox, "/workspace/repo") is True

    def test_not_a_repo(self) -> None:
        sandbox = _FakeSandbox(
            {"test -d": ExecuteResponse(output="", exit_code=1, truncated=False)}
        )
        assert is_valid_git_repo(sandbox, "/workspace/repo") is False

    def test_dir_exists_but_no_git(self) -> None:
        sandbox = _FakeSandbox(
            {"test -d": ExecuteResponse(output="\n", exit_code=0, truncated=False)}
        )
        assert is_valid_git_repo(sandbox, "/workspace/repo") is False


# ---------------------------------------------------------------------------
# remove_directory
# ---------------------------------------------------------------------------


class TestRemoveDirectory:
    def test_success(self) -> None:
        sandbox = _FakeSandbox()
        assert remove_directory(sandbox, "/workspace/old-repo") is True
        assert any("rm -rf" in cmd for cmd in sandbox.commands)

    def test_failure(self) -> None:
        sandbox = _FakeSandbox(
            {"rm -rf": ExecuteResponse(output="permission denied", exit_code=1, truncated=False)}
        )
        assert remove_directory(sandbox, "/workspace/protected") is False


# ---------------------------------------------------------------------------
# git_has_uncommitted_changes
# ---------------------------------------------------------------------------


class TestGitHasUncommittedChanges:
    def test_clean_repo(self) -> None:
        sandbox = _FakeSandbox(
            {"git status --porcelain": ExecuteResponse(output="", exit_code=0, truncated=False)}
        )
        assert git_has_uncommitted_changes(sandbox, "/repo") is False

    def test_dirty_repo(self) -> None:
        sandbox = _FakeSandbox(
            {
                "git status --porcelain": ExecuteResponse(
                    output="M agent/server.py\n?? new_file.py\n",
                    exit_code=0,
                    truncated=False,
                )
            }
        )
        assert git_has_uncommitted_changes(sandbox, "/repo") is True

    def test_command_failure(self) -> None:
        sandbox = _FakeSandbox(
            {
                "git status --porcelain": ExecuteResponse(
                    output="fatal: not a git repository",
                    exit_code=128,
                    truncated=False,
                )
            }
        )
        assert git_has_uncommitted_changes(sandbox, "/repo") is False


# ---------------------------------------------------------------------------
# git_has_unpushed_commits
# ---------------------------------------------------------------------------


class TestGitHasUnpushedCommits:
    def test_no_unpushed(self) -> None:
        sandbox = _FakeSandbox(
            {"git log": ExecuteResponse(output="", exit_code=0, truncated=False)}
        )
        assert git_has_unpushed_commits(sandbox, "/repo") is False

    def test_has_unpushed(self) -> None:
        sandbox = _FakeSandbox(
            {
                "git log": ExecuteResponse(
                    output="abc1234 fix something\ndef5678 add feature\n",
                    exit_code=0,
                    truncated=False,
                )
            }
        )
        assert git_has_unpushed_commits(sandbox, "/repo") is True


# ---------------------------------------------------------------------------
# git_current_branch
# ---------------------------------------------------------------------------


class TestGitCurrentBranch:
    def test_returns_branch_name(self) -> None:
        sandbox = _FakeSandbox(
            {
                "git rev-parse --abbrev-ref HEAD": ExecuteResponse(
                    output="feature/my-branch\n", exit_code=0, truncated=False
                )
            }
        )
        assert git_current_branch(sandbox, "/repo") == "feature/my-branch"

    def test_returns_empty_on_failure(self) -> None:
        sandbox = _FakeSandbox(
            {
                "git rev-parse --abbrev-ref HEAD": ExecuteResponse(
                    output="", exit_code=1, truncated=False
                )
            }
        )
        assert git_current_branch(sandbox, "/repo") == ""


# ---------------------------------------------------------------------------
# git_checkout_branch
# ---------------------------------------------------------------------------


class TestGitCheckoutBranch:
    def test_checkout_B_succeeds(self) -> None:
        sandbox = _FakeSandbox(
            {"git checkout -B": ExecuteResponse(output="", exit_code=0, truncated=False)}
        )
        assert git_checkout_branch(sandbox, "/repo", "open-swe/thread-1") is True

    def test_falls_back_to_checkout_b(self) -> None:
        sandbox = _FakeSandbox(
            {
                "git checkout -B": ExecuteResponse(output="", exit_code=1, truncated=False),
                "git checkout -b": ExecuteResponse(output="", exit_code=0, truncated=False),
            }
        )
        assert git_checkout_branch(sandbox, "/repo", "open-swe/thread-1") is True

    def test_all_fallbacks_fail(self) -> None:
        sandbox = _FakeSandbox(
            {"git checkout": ExecuteResponse(output="error", exit_code=1, truncated=False)}
        )
        assert git_checkout_branch(sandbox, "/repo", "nonexistent") is False


# ---------------------------------------------------------------------------
# git_config_user
# ---------------------------------------------------------------------------


class TestGitConfigUser:
    def test_sets_name_and_email(self) -> None:
        sandbox = _FakeSandbox()
        git_config_user(sandbox, "/repo", "bot", "bot@example.com")
        assert any("user.name" in cmd and "bot" in cmd for cmd in sandbox.commands)
        assert any("user.email" in cmd and "bot@example.com" in cmd for cmd in sandbox.commands)


# ---------------------------------------------------------------------------
# git_add_all / git_commit
# ---------------------------------------------------------------------------


class TestGitAddAndCommit:
    def test_add_all(self) -> None:
        sandbox = _FakeSandbox()
        result = git_add_all(sandbox, "/repo")
        assert result.exit_code == 0
        assert any("git add -A" in cmd for cmd in sandbox.commands)

    def test_commit(self) -> None:
        sandbox = _FakeSandbox()
        result = git_commit(sandbox, "/repo", "fix: something")
        assert result.exit_code == 0
        assert any("git commit -m" in cmd for cmd in sandbox.commands)

    def test_commit_message_is_shell_escaped(self) -> None:
        sandbox = _FakeSandbox()
        git_commit(sandbox, "/repo", "fix: handle 'quotes' and $pecial chars")
        commit_cmd = [c for c in sandbox.commands if "git commit" in c][0]
        assert "$pecial" not in commit_cmd or "'" in commit_cmd


# ---------------------------------------------------------------------------
# git_get_remote_url
# ---------------------------------------------------------------------------


class TestGitGetRemoteUrl:
    def test_returns_url(self) -> None:
        sandbox = _FakeSandbox(
            {
                "git remote get-url origin": ExecuteResponse(
                    output="https://github.com/org/repo.git\n", exit_code=0, truncated=False
                )
            }
        )
        assert git_get_remote_url(sandbox, "/repo") == "https://github.com/org/repo.git"

    def test_returns_none_on_failure(self) -> None:
        sandbox = _FakeSandbox(
            {"git remote get-url origin": ExecuteResponse(output="", exit_code=1, truncated=False)}
        )
        assert git_get_remote_url(sandbox, "/repo") is None


# ---------------------------------------------------------------------------
# setup_git_credentials / cleanup_git_credentials
# ---------------------------------------------------------------------------


class TestGitCredentials:
    def test_setup_writes_cred_file(self) -> None:
        sandbox = _FakeSandbox()
        setup_git_credentials(sandbox, "ghp_test123")
        assert _CRED_FILE_PATH in sandbox.written_files
        assert "ghp_test123" in sandbox.written_files[_CRED_FILE_PATH]
        assert any("chmod 600" in cmd for cmd in sandbox.commands)

    def test_cleanup_removes_cred_file(self) -> None:
        sandbox = _FakeSandbox()
        cleanup_git_credentials(sandbox)
        assert any("rm -f" in cmd and _CRED_FILE_PATH in cmd for cmd in sandbox.commands)


# ---------------------------------------------------------------------------
# git_push
# ---------------------------------------------------------------------------


class TestGitPush:
    def test_push_without_token(self) -> None:
        sandbox = _FakeSandbox()
        result = git_push(sandbox, "/repo", "my-branch")
        assert result.exit_code == 0
        assert any("git push origin" in cmd for cmd in sandbox.commands)
        assert _CRED_FILE_PATH not in sandbox.written_files

    def test_push_with_token_sets_up_and_cleans_credentials(self) -> None:
        sandbox = _FakeSandbox()
        result = git_push(sandbox, "/repo", "my-branch", "ghp_token")
        assert result.exit_code == 0
        assert _CRED_FILE_PATH in sandbox.written_files
        assert any("rm -f" in cmd for cmd in sandbox.commands)

    def test_push_with_token_cleans_up_even_on_failure(self) -> None:
        sandbox = _FakeSandbox(
            {"credential.helper": ExecuteResponse(output="rejected", exit_code=1, truncated=False)}
        )
        result = git_push(sandbox, "/repo", "my-branch", "ghp_token")
        assert result.exit_code == 1
        cleanup_cmds = [c for c in sandbox.commands if "rm -f" in c]
        assert len(cleanup_cmds) >= 1
