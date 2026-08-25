import base64
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent import server
from agent.integrations.corridor_commit_scan import (
    CORRIDOR_HOOKS_PATH,
    corridor_commit_scanning_enabled,
    corridor_hook_cleanup_command,
    corridor_hook_setup_command,
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
