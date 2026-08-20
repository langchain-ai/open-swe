"""Tests for sandbox git timeout and failure hints."""

import pytest

from agent.utils.sandbox_git import (
    git_command_failure_hints,
    inject_git_curl_ipv4,
    sandbox_git_clone_depth_args,
    sandbox_git_force_ipv4_enabled,
)


def test_git_command_failure_hints_ado_tf401019() -> None:
    out = "remote: TF401019: The Git repository with name or identifier X does not exist"
    h = git_command_failure_hints(git_output=out, is_azure_devops=True)
    assert "Code (Read)" in h
    assert "Hint:" in h


def test_git_command_failure_hints_github_skips_ado_hint() -> None:
    out = "remote: TF401019: ..."  # ADO-style line; GitHub path should not add ADO PAT hint
    h = git_command_failure_hints(git_output=out, is_azure_devops=False)
    assert "Code (Read)" not in h


def test_git_command_failure_hints_timeout() -> None:
    h = git_command_failure_hints(
        git_output="error: RPC failed; curl 28 Operation timed out", is_azure_devops=False
    )
    assert "SANDBOX_GIT_TIMEOUT_SEC" in h


def test_git_command_failure_hints_dns() -> None:
    h = git_command_failure_hints(
        git_output="Could not resolve host dev.azure.com", is_azure_devops=True
    )
    assert "DNS" in h or "egress" in h


def test_git_command_failure_hints_resolving_timed_out() -> None:
    h = git_command_failure_hints(
        git_output="fatal: Resolving timed out after 300000 ms",
        is_azure_devops=True,
    )
    assert "IPv6" in h or "curl" in h.lower()


def test_inject_git_curl_ipv4_clone_style(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SANDBOX_GIT_FORCE_IPV4", raising=False)
    assert sandbox_git_force_ipv4_enabled() is True
    out = inject_git_curl_ipv4("git -c foo=bar clone https://x/y z")
    assert out.startswith("git -c http.curlOptions=-4 -c foo=bar clone")


def test_inject_git_curl_ipv4_pull_style(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SANDBOX_GIT_FORCE_IPV4", raising=False)
    cmd = "cd /r && git -c foo=bar pull origin $(git rev-parse --abbrev-ref HEAD)"
    out = inject_git_curl_ipv4(cmd)
    assert "&& git -c http.curlOptions=-4 -c foo=bar pull" in out


def test_inject_git_curl_ipv4_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_GIT_FORCE_IPV4", "0")
    assert sandbox_git_force_ipv4_enabled() is False
    assert inject_git_curl_ipv4("git clone x y") == "git clone x y"


def test_sandbox_git_clone_depth_args_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SANDBOX_GIT_CLONE_DEPTH", raising=False)
    assert sandbox_git_clone_depth_args() == ""


def test_sandbox_git_clone_depth_args_shallow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_GIT_CLONE_DEPTH", "1")
    assert sandbox_git_clone_depth_args() == " --depth 1"
