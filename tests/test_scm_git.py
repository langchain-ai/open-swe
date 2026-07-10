"""Tests for Azure DevOps git auth injection and branch validation."""

from __future__ import annotations

import pytest

from agent.utils.scm_git import (
    inject_azure_devops_git_auth,
    validate_git_branch_short_name,
)


def test_validate_git_branch_short_name_accepts_safe_names() -> None:
    assert validate_git_branch_short_name("feature/my-branch_1") == "feature/my-branch_1"


def test_validate_git_branch_short_name_rejects_shell_metacharacters() -> None:
    with pytest.raises(ValueError, match="Unsafe"):
        validate_git_branch_short_name("feature/$(whoami)")


def test_inject_azure_devops_git_auth_prefixes_git_invocations() -> None:
    """Fallback path for non-LangSmith sandboxes (local/tests)."""
    cmd = "cd /repo && git push origin main"
    out = inject_azure_devops_git_auth(cmd, "secret-pat")
    assert "http.extraHeader=Authorization: Basic" in out
    assert out.startswith("cd /repo && git -c ")
    assert " push origin main" in out
    assert ".git/config" not in out


def test_inject_azure_devops_git_auth_noop_without_pat() -> None:
    cmd = "cd /repo && git push origin main"
    assert inject_azure_devops_git_auth(cmd, None) == cmd


def test_inject_azure_devops_git_auth_compound_command() -> None:
    cmd = "git status && git pull origin main"
    out = inject_azure_devops_git_auth(cmd, "pat")
    assert out.count("git -c ") == 2
