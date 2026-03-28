from __future__ import annotations

import importlib
from types import SimpleNamespace

commit_tool = importlib.import_module("agent.tools.commit_and_open_pr")


def test_commit_and_open_pr_falls_back_to_runtime_token_resolution(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class _FakeSandbox:
        def execute(self, command: str):
            return SimpleNamespace(exit_code=0, output="")

    async def fake_resolve_github_token(config, thread_id: str):
        calls["resolved_thread_id"] = thread_id
        return "runtime-token", ""

    async def fake_get_github_default_branch(repo_owner: str, repo_name: str, github_token: str):
        calls["default_branch_token"] = github_token
        return "main"

    async def fake_create_github_pr(**kwargs):
        calls["pr_token"] = kwargs["github_token"]
        return "https://github.com/parimple/BohtPY/pull/999", 999, False

    def fake_git_push(sandbox, repo_dir, branch, github_token):
        calls["push_token"] = github_token
        return SimpleNamespace(exit_code=0, output="")

    def fake_git_checkout_branch_from_start_point(sandbox, repo_dir, branch, start_point):
        calls["checkout_branch"] = branch
        calls["checkout_start_point"] = start_point
        return True

    monkeypatch.setattr(
        commit_tool,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "thread-123",
                "repo": {"owner": "parimple", "name": "BohtPY"},
                "source": "github",
                "github_login": "parimple",
            },
            "metadata": {},
        },
    )
    monkeypatch.setattr(commit_tool, "get_sandbox_backend_sync", lambda thread_id: _FakeSandbox())
    monkeypatch.setattr(commit_tool, "resolve_repo_dir", lambda sandbox_backend, repo_name: "/tmp/repo")
    monkeypatch.setattr(commit_tool, "get_github_token", lambda: None)
    monkeypatch.setattr(commit_tool, "resolve_github_token", fake_resolve_github_token)
    monkeypatch.setattr(commit_tool, "resolve_triggering_user_identity", lambda config, token: None)
    monkeypatch.setattr(commit_tool, "add_pr_collaboration_note", lambda body, identity: body)
    monkeypatch.setattr(commit_tool, "git_has_uncommitted_changes", lambda sandbox, repo_dir: True)
    monkeypatch.setattr(commit_tool, "git_fetch_origin", lambda sandbox, repo_dir: None)
    monkeypatch.setattr(commit_tool, "git_has_unpushed_commits", lambda sandbox, repo_dir: False)
    monkeypatch.setattr(commit_tool, "git_current_branch", lambda sandbox, repo_dir: "open-swe/thread-123")
    monkeypatch.setattr(
        commit_tool,
        "git_checkout_branch_from_start_point",
        fake_git_checkout_branch_from_start_point,
    )
    monkeypatch.setattr(commit_tool, "git_config_user", lambda sandbox, repo_dir, name, email: None)
    monkeypatch.setattr(commit_tool, "git_add_all", lambda sandbox, repo_dir: None)
    monkeypatch.setattr(
        commit_tool,
        "add_user_coauthor_trailer",
        lambda message, identity: message,
    )
    monkeypatch.setattr(
        commit_tool,
        "git_commit",
        lambda sandbox, repo_dir, message: SimpleNamespace(exit_code=0, output=""),
    )
    monkeypatch.setattr(
        commit_tool,
        "git_push",
        fake_git_push,
    )
    monkeypatch.setattr(commit_tool, "get_github_default_branch", fake_get_github_default_branch)
    monkeypatch.setattr(commit_tool, "create_github_pr", fake_create_github_pr)

    result = commit_tool.commit_and_open_pr(
        title="feat: add smoke test file",
        body="## Description\nAdds the smoke test file.\n\n## Test Plan\n- [ ] Verify the file exists\n",
    )

    assert result["success"] is True
    assert calls["resolved_thread_id"] == "thread-123"
    assert calls["push_token"] == "runtime-token"
    assert calls["default_branch_token"] == "runtime-token"
    assert calls["pr_token"] == "runtime-token"


def test_commit_and_open_pr_creates_new_branch_from_default_branch(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class _FakeSandbox:
        def execute(self, command: str):
            return SimpleNamespace(exit_code=0, output="")

    async def fake_get_github_default_branch(repo_owner: str, repo_name: str, github_token: str):
        calls["default_branch_lookup"] = (repo_owner, repo_name, github_token)
        return "main"

    async def fake_create_github_pr(**kwargs):
        return "https://github.com/parimple/BohtPY/pull/1000", 1000, False

    def fake_git_checkout_branch_from_start_point(sandbox, repo_dir, branch, start_point):
        calls["checkout_branch"] = branch
        calls["checkout_start_point"] = start_point
        return True

    monkeypatch.setattr(
        commit_tool,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "thread-456",
                "repo": {"owner": "parimple", "name": "BohtPY"},
            },
            "metadata": {},
        },
    )
    monkeypatch.setattr(commit_tool, "get_sandbox_backend_sync", lambda thread_id: _FakeSandbox())
    monkeypatch.setattr(commit_tool, "resolve_repo_dir", lambda sandbox_backend, repo_name: "/tmp/repo")
    monkeypatch.setattr(commit_tool, "get_github_token", lambda: "github-token")
    monkeypatch.setattr(commit_tool, "resolve_triggering_user_identity", lambda config, token: None)
    monkeypatch.setattr(commit_tool, "add_pr_collaboration_note", lambda body, identity: body)
    monkeypatch.setattr(commit_tool, "git_has_uncommitted_changes", lambda sandbox, repo_dir: True)
    monkeypatch.setattr(commit_tool, "git_fetch_origin", lambda sandbox, repo_dir: None)
    monkeypatch.setattr(commit_tool, "git_has_unpushed_commits", lambda sandbox, repo_dir: False)
    monkeypatch.setattr(
        commit_tool,
        "git_current_branch",
        lambda sandbox, repo_dir: "open-swe/older-thread",
    )
    monkeypatch.setattr(
        commit_tool,
        "git_checkout_branch_from_start_point",
        fake_git_checkout_branch_from_start_point,
    )
    monkeypatch.setattr(commit_tool, "git_config_user", lambda sandbox, repo_dir, name, email: None)
    monkeypatch.setattr(commit_tool, "git_add_all", lambda sandbox, repo_dir: None)
    monkeypatch.setattr(commit_tool, "add_user_coauthor_trailer", lambda message, identity: message)
    monkeypatch.setattr(
        commit_tool,
        "git_commit",
        lambda sandbox, repo_dir, message: SimpleNamespace(exit_code=0, output=""),
    )
    monkeypatch.setattr(
        commit_tool,
        "git_push",
        lambda sandbox, repo_dir, branch, github_token: SimpleNamespace(exit_code=0, output=""),
    )
    monkeypatch.setattr(commit_tool, "get_github_default_branch", fake_get_github_default_branch)
    monkeypatch.setattr(commit_tool, "create_github_pr", fake_create_github_pr)

    result = commit_tool.commit_and_open_pr(
        title="feat: add smoke test file",
        body="## Description\nAdds the smoke test file.\n\n## Test Plan\n- [ ] Verify the file exists\n",
    )

    assert result["success"] is True
    assert calls["checkout_branch"] == "open-swe/thread-456"
    assert calls["checkout_start_point"] == "origin/main"
