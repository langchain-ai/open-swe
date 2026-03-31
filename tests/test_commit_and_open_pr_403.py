"""Tests that commit_and_open_pr returns a PERMANENT_FAILURE message on 403 push errors."""
from unittest.mock import MagicMock, patch

from deepagents.backends.protocol import ExecuteResponse


def _make_push_result(exit_code: int, output: str) -> ExecuteResponse:
    return ExecuteResponse(output=output, exit_code=exit_code, truncated=False)


def _make_config(thread_id: str = "test-thread") -> dict:
    return {
        "configurable": {
            "thread_id": thread_id,
            "repo": {"owner": "langchain-ai", "name": "docs"},
        },
        "metadata": {},
    }


@patch("agent.tools.commit_and_open_pr.get_config")
@patch("agent.tools.commit_and_open_pr.get_sandbox_backend_sync")
@patch("agent.tools.commit_and_open_pr.resolve_repo_dir", return_value="/repo/docs")
@patch("agent.tools.commit_and_open_pr.git_has_uncommitted_changes", return_value=False)
@patch("agent.tools.commit_and_open_pr.git_fetch_origin")
@patch("agent.tools.commit_and_open_pr.git_has_unpushed_commits", return_value=True)
@patch("agent.tools.commit_and_open_pr.git_current_branch", return_value="open-swe/test-thread")
@patch("agent.tools.commit_and_open_pr.git_checkout_branch", return_value=True)
@patch("agent.tools.commit_and_open_pr.git_config_user")
@patch("agent.tools.commit_and_open_pr.git_add_all")
@patch("agent.tools.commit_and_open_pr.get_github_token", return_value="ghp_token")
@patch("agent.tools.commit_and_open_pr.git_push")
def test_403_push_returns_permanent_failure(
    mock_git_push,
    mock_get_token,
    mock_git_add_all,
    mock_git_config_user,
    mock_git_checkout_branch,
    mock_git_current_branch,
    mock_git_has_unpushed,
    mock_git_fetch_origin,
    mock_git_has_uncommitted,
    mock_resolve_repo_dir,
    mock_get_sandbox,
    mock_get_config,
) -> None:
    from agent.tools.commit_and_open_pr import commit_and_open_pr

    mock_get_config.return_value = _make_config()
    mock_get_sandbox.return_value = MagicMock()
    mock_git_push.return_value = _make_push_result(
        exit_code=128,
        output=(
            "remote: Permission to langchain-ai/docs.git denied to hinthornw.\n"
            "fatal: unable to access 'https://github.com/langchain-ai/docs/': "
            "The requested URL returned error: 403"
        ),
    )

    result = commit_and_open_pr(title="fix: something", body="## Description\nfoo\n\n## Test Plan\n- [ ] check")

    assert result["success"] is False
    assert result["pr_url"] is None
    error = result["error"]
    assert "PERMANENT_FAILURE" in error
    assert "do not retry" in error
    assert "403" in error


@patch("agent.tools.commit_and_open_pr.get_config")
@patch("agent.tools.commit_and_open_pr.get_sandbox_backend_sync")
@patch("agent.tools.commit_and_open_pr.resolve_repo_dir", return_value="/repo/docs")
@patch("agent.tools.commit_and_open_pr.git_has_uncommitted_changes", return_value=False)
@patch("agent.tools.commit_and_open_pr.git_fetch_origin")
@patch("agent.tools.commit_and_open_pr.git_has_unpushed_commits", return_value=True)
@patch("agent.tools.commit_and_open_pr.git_current_branch", return_value="open-swe/test-thread")
@patch("agent.tools.commit_and_open_pr.git_checkout_branch", return_value=True)
@patch("agent.tools.commit_and_open_pr.git_config_user")
@patch("agent.tools.commit_and_open_pr.git_add_all")
@patch("agent.tools.commit_and_open_pr.get_github_token", return_value="ghp_token")
@patch("agent.tools.commit_and_open_pr.git_push")
def test_non_403_push_failure_returns_regular_error(
    mock_git_push,
    mock_get_token,
    mock_git_add_all,
    mock_git_config_user,
    mock_git_checkout_branch,
    mock_git_current_branch,
    mock_git_has_unpushed,
    mock_git_fetch_origin,
    mock_git_has_uncommitted,
    mock_resolve_repo_dir,
    mock_get_sandbox,
    mock_get_config,
) -> None:
    from agent.tools.commit_and_open_pr import commit_and_open_pr

    mock_get_config.return_value = _make_config()
    mock_get_sandbox.return_value = MagicMock()
    mock_git_push.return_value = _make_push_result(
        exit_code=1,
        output="error: failed to push some refs to 'origin'",
    )

    result = commit_and_open_pr(title="fix: something", body="## Description\nfoo\n\n## Test Plan\n- [ ] check")

    assert result["success"] is False
    assert result["pr_url"] is None
    error = result["error"]
    assert "PERMANENT_FAILURE" not in error
    assert error.startswith("Git push failed:")
