from __future__ import annotations

import shlex
from types import SimpleNamespace

from agent.utils import github


class FakeSandboxBackend:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.writes: list[tuple[str, str]] = []

    def execute(self, command: str) -> SimpleNamespace:
        self.commands.append(command)
        return SimpleNamespace(exit_code=0, output="")

    def write(self, path: str, content: str) -> None:
        self.writes.append((path, content))


def test_git_checkout_existing_branch_quotes_repo_dir_and_branch() -> None:
    sandbox = FakeSandboxBackend()
    repo_dir = "/tmp/repo; curl attacker"
    branch = "main; curl attacker"

    github.git_checkout_existing_branch(sandbox, repo_dir, branch)

    assert sandbox.commands == [f"cd {shlex.quote(repo_dir)} && git checkout {shlex.quote(branch)}"]


def test_git_checkout_branch_returns_true_on_success() -> None:
    sandbox = FakeSandboxBackend()
    ok, err = github.git_checkout_branch(sandbox, "/tmp/repo", "my-branch")
    assert ok is True
    assert err == ""


def test_git_checkout_branch_returns_false_with_error_output_on_failure() -> None:
    class FailingSandbox(FakeSandboxBackend):
        def execute(self, command: str) -> SimpleNamespace:
            self.commands.append(command)
            return SimpleNamespace(exit_code=1, output="error: pathspec did not match")

    sandbox = FailingSandbox()
    ok, err = github.git_checkout_branch(sandbox, "/tmp/repo", "my-branch")
    assert ok is False
    assert "pathspec did not match" in err
