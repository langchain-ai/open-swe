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

    assert sandbox.commands == [
        f"cd {shlex.quote(repo_dir)} && git checkout {shlex.quote(branch)}"
    ]


def test_git_pull_branch_quotes_repo_dir_and_branch_when_using_credentials() -> None:
    sandbox = FakeSandboxBackend()
    repo_dir = "/tmp/repo; curl attacker"
    branch = "main; curl attacker"

    github.git_pull_branch(sandbox, repo_dir, branch, github_token="secret-token")

    assert sandbox.writes == [
        (github._CRED_FILE_PATH, "https://git:secret-token@github.com\n")
    ]
    assert sandbox.commands == [
        f"chmod 600 {github._CRED_FILE_PATH}",
        (
            f"cd {shlex.quote(repo_dir)} && git -c "
            f"credential.helper={shlex.quote(f'store --file={github._CRED_FILE_PATH}')}"
            f" pull origin {shlex.quote(branch)}"
        ),
        f"rm -f {github._CRED_FILE_PATH}",
    ]
