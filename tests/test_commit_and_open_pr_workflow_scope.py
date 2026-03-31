"""Tests for workflow-scope push error detection in commit_and_open_pr."""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from agent.tools.commit_and_open_pr import commit_and_open_pr


def _make_exec_result(exit_code: int, output: str) -> MagicMock:
    result = MagicMock()
    result.exit_code = exit_code
    result.output = output
    return result


WORKFLOW_SCOPE_OUTPUT = (
    "To https://github.com/langchain-ai/langchainplus.git\n"
    " ! [remote rejected]       open-swe/abc -> open-swe/abc "
    "(Unable to determine if workflow can be created or updated due to timeout; "
    "`workflows` scope may be required.)\n"
    "error: failed to push some refs to 'https://github.com/langchain-ai/langchainplus.git'\n"
)


def _run_with_push_result(push_result: MagicMock) -> dict:
    """Run commit_and_open_pr with all external calls mocked, using the given push_result."""
    config = {
        "configurable": {
            "thread_id": "test-thread-id",
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
        },
        "metadata": {},
    }
    sandbox = MagicMock()
    sandbox.execute.return_value = _make_exec_result(0, "")

    patches = [
        patch("agent.tools.commit_and_open_pr.get_config", return_value=config),
        patch("agent.tools.commit_and_open_pr.get_sandbox_backend_sync", return_value=sandbox),
        patch("agent.tools.commit_and_open_pr.git_has_uncommitted_changes", return_value=True),
        patch("agent.tools.commit_and_open_pr.git_fetch_origin"),
        patch("agent.tools.commit_and_open_pr.git_has_unpushed_commits", return_value=False),
        patch(
            "agent.tools.commit_and_open_pr.get_github_app_installation_token",
            return_value="token",
        ),
        patch("agent.tools.commit_and_open_pr.get_github_token", return_value="gh-token"),
        patch(
            "agent.tools.commit_and_open_pr.resolve_triggering_user_identity", return_value=None
        ),
        patch(
            "agent.tools.commit_and_open_pr.add_pr_collaboration_note",
            side_effect=lambda body, _: body,
        ),
        patch(
            "agent.tools.commit_and_open_pr.add_user_coauthor_trailer",
            side_effect=lambda msg, _: msg,
        ),
        patch(
            "agent.tools.commit_and_open_pr.git_current_branch",
            return_value="open-swe/test-thread-id",
        ),
        patch("agent.tools.commit_and_open_pr.git_config_user"),
        patch("agent.tools.commit_and_open_pr.git_add_all"),
        patch(
            "agent.tools.commit_and_open_pr.git_commit",
            return_value=_make_exec_result(0, ""),
        ),
        patch("agent.tools.commit_and_open_pr.git_push", return_value=push_result),
        patch("agent.tools.commit_and_open_pr.asyncio.run", return_value="token"),
    ]

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return commit_and_open_pr(title="fix: test", body="## Description\ntest")


class TestWorkflowScopePushError:
    def test_workflow_scope_error_returns_actionable_message(self):
        """Workflow-scope push failure returns a clear message telling the agent to remove
        .github/workflows/ files rather than a generic 'Git push failed:' that causes retries."""
        result = _run_with_push_result(_make_exec_result(1, WORKFLOW_SCOPE_OUTPUT))

        assert result["success"] is False
        assert ".github/workflows/" in result["error"]
        assert "Remove any .github/workflows/" in result["error"]
        assert result["pr_url"] is None
        # Must NOT be the raw git output dump that causes blind agent retries
        assert "remote rejected" not in result["error"]

    def test_non_workflow_push_error_returns_generic_message(self):
        """Non-workflow push failures still return the original generic error message."""
        generic_output = (
            "To https://github.com/langchain-ai/open-swe.git\n"
            " ! [remote rejected] open-swe/abc -> open-swe/abc (permission denied)\n"
            "error: failed to push some refs\n"
        )
        result = _run_with_push_result(_make_exec_result(1, generic_output))

        assert result["success"] is False
        assert result["error"].startswith("Git push failed:")
        assert ".github/workflows/" not in result["error"]
