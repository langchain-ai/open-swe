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


