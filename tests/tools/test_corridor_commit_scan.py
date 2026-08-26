import base64
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent import server
from agent.integrations.corridor_commit_scan import (
    CORRIDOR_HOOKS_PATH,
    corridor_commit_scanning_enabled,
    corridor_hook_cleanup_command,
    corridor_hook_script,
    corridor_hook_setup_command,
    validate_corridor_commit_scanning_sandbox,
)
from agent.prompt import construct_system_prompt


@pytest.fixture(autouse=True)
def clear_corridor_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CORRIDOR_API_KEY",
        "CORRIDOR_API_TOKEN",
        "CORRIDOR_MCP_TOKEN",
        "CORRIDOR_TOKEN",
        "CORRIDOR_COMMIT_SCANNING_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)


def test_commit_scanning_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    assert corridor_commit_scanning_enabled() is False
    monkeypatch.setenv("CORRIDOR_COMMIT_SCANNING_ENABLED", "yes")
    assert corridor_commit_scanning_enabled() is True


def test_commit_scanning_requires_langsmith(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORRIDOR_COMMIT_SCANNING_ENABLED", "true")
    with pytest.raises(ValueError, match="SANDBOX_TYPE=langsmith"):
        validate_corridor_commit_scanning_sandbox("local")
    validate_corridor_commit_scanning_sandbox("langsmith")


def test_hook_chains_repo_pre_commit_before_scanning() -> None:
    command = corridor_hook_setup_command()
    encoded = re.search(r"printf %s ([A-Za-z0-9+/=]+) \| base64 -d", command)
    assert encoded is not None
    hook = base64.b64decode(encoded.group(1)).decode()

    repo_hook_call = '"$repo_hook" "$@"'
    assert hook.index(repo_hook_call) < hook.index("corridor scan --staged")
    assert f"exec {repo_hook_call}" in hook
    assert "base64 -d" in command
    assert CORRIDOR_HOOKS_PATH in command
    assert "could not be configured; continuing" in command
    assert "open-swe-corridor-hook" in command
    assert "commit-msg" in command
    assert "pre-push" in command
    assert "git config --local" not in command


def test_setup_without_corridor_warns_and_does_not_install(tmp_path: Path) -> None:
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    git = subprocess.run(
        ["which", "git"], check=True, capture_output=True, text=True
    ).stdout.strip()
    (bin_dir / "git").symlink_to(git)

    result = subprocess.run(
        ["/bin/sh", "-c", corridor_hook_setup_command()],
        check=True,
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": str(bin_dir)},
    )

    assert "could not be configured; continuing" in result.stderr
    hooks_path = subprocess.run(
        [git, "config", "--global", "--get", "core.hooksPath"],
        capture_output=True,
        text=True,
        env={"HOME": str(home)},
    )
    assert hooks_path.returncode == 1


def test_real_commit_scans_changes_staged_by_repo_hook(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    hooks = tmp_path / "managed-hooks"
    bin_dir = tmp_path / "bin"
    repo.mkdir()
    hooks.mkdir()
    bin_dir.mkdir()
    scanned = tmp_path / "scanned"

    dispatcher = hooks / "open-swe-corridor-hook"
    dispatcher.write_text(corridor_hook_script())
    dispatcher.chmod(0o700)
    (hooks / "pre-commit").symlink_to(dispatcher.name)
    corridor = bin_dir / "corridor"
    corridor.write_text(f'#!/bin/sh\ngit show :tracked.txt > "{scanned}"\n')
    corridor.chmod(0o700)

    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True, env=env
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.hooksPath", str(hooks)], check=True, env=env
    )
    tracked = repo / "tracked.txt"
    tracked.write_text("before\n")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True, env=env)

    repo_hook = repo / ".git" / "hooks" / "pre-commit"
    repo_hook.write_text('#!/bin/sh\nprintf "after\\n" > tracked.txt\ngit add tracked.txt\n')
    repo_hook.chmod(0o700)
    tracked.write_text("pending\n")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "update"], check=True, env=env)

    assert scanned.read_text() == "after\n"
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "show", "HEAD:tracked.txt"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        ).stdout
        == "after\n"
    )


def test_cleanup_only_removes_the_managed_hooks_path() -> None:
    command = corridor_hook_cleanup_command()
    assert f'= "{CORRIDOR_HOOKS_PATH}"' in command
    assert "--unset core.hooksPath" in command


async def test_git_setup_installs_hook_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORRIDOR_COMMIT_SCANNING_ENABLED", "true")
    backend = MagicMock(aexecute=AsyncMock(return_value=SimpleNamespace(exit_code=0, output="")))

    await server._configure_git_identity(backend)

    command = backend.aexecute.await_args.args[0]
    assert corridor_hook_setup_command() in command


async def test_git_setup_removes_managed_hook_when_disabled() -> None:
    backend = MagicMock(aexecute=AsyncMock(return_value=SimpleNamespace(exit_code=0, output="")))

    await server._configure_git_identity(backend)

    command = backend.aexecute.await_args.args[0]
    assert corridor_hook_cleanup_command() in command


async def test_git_setup_does_not_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORRIDOR_COMMIT_SCANNING_ENABLED", "true")
    backend = MagicMock(
        aexecute=AsyncMock(return_value=SimpleNamespace(exit_code=1, output="missing CLI"))
    )

    await server._configure_git_identity(backend)


def test_prompt_prohibits_bypassing_enabled_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    prompt = construct_system_prompt(working_dir="/workspace", corridor_commit_scanning=True)
    assert "git commit --no-verify" in prompt
    assert "never remove, replace, disable, or bypass it" in prompt

    prompt = construct_system_prompt(working_dir="/workspace")
    assert "git commit --no-verify" not in prompt
