from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent import server
from agent.integrations.corridor_commit_scan import (
    CORRIDOR_HOOKS_PATH,
    corridor_commit_scanning_enabled,
    corridor_hook_cleanup_command,
    corridor_hook_setup_command,
    validate_corridor_commit_scanning_config,
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


def test_enabled_commit_scanning_requires_langsmith_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORRIDOR_COMMIT_SCANNING_ENABLED", "true")
    with pytest.raises(ValueError, match="SANDBOX_TYPE=langsmith"):
        validate_corridor_commit_scanning_config("local")
    with pytest.raises(ValueError, match="CORRIDOR_API_KEY"):
        validate_corridor_commit_scanning_config("langsmith")
    monkeypatch.setenv("CORRIDOR_API_KEY", "secret")
    validate_corridor_commit_scanning_config("langsmith")


def test_hook_scans_then_chains_repo_hooks() -> None:
    command = corridor_hook_setup_command()
    assert "corridor scan --staged" not in command
    assert "base64 -d" in command
    assert CORRIDOR_HOOKS_PATH in command
    assert "will not replace the existing global hooks path" in command
    assert "open-swe-corridor-hook" in command
    assert "pre-commit" in command
    assert "commit-msg" in command
    assert "pre-push" in command


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


async def test_git_setup_fails_closed_when_hook_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORRIDOR_COMMIT_SCANNING_ENABLED", "true")
    backend = MagicMock(
        aexecute=AsyncMock(return_value=SimpleNamespace(exit_code=1, output="missing CLI"))
    )

    with pytest.raises(RuntimeError, match="missing CLI"):
        await server._configure_git_identity(backend)


def test_prompt_prohibits_bypassing_enabled_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    prompt = construct_system_prompt(working_dir="/workspace", corridor_commit_scanning=True)
    assert "git commit --no-verify" in prompt
    assert "Never remove, replace, disable, or bypass it" in prompt

    prompt = construct_system_prompt(working_dir="/workspace")
    assert "git commit --no-verify" not in prompt


def test_validation_uses_corridor_token_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORRIDOR_COMMIT_SCANNING_ENABLED", "true")
    monkeypatch.setenv("CORRIDOR_API_TOKEN", "secret")
    validate_corridor_commit_scanning_config("langsmith")
