"""Corridor pre-commit scanning for managed sandboxes."""

import base64
import os

from .corridor_mcp import corridor_token

CORRIDOR_COMMIT_SCANNING_ENV = "CORRIDOR_COMMIT_SCANNING_ENABLED"
CORRIDOR_HOOKS_PATH = "/root/.config/open-swe/git-hooks"
CORRIDOR_API_KEY_PLACEHOLDER = "proxy-injected"

_HOOK_NAMES = (
    "applypatch-msg",
    "pre-applypatch",
    "post-applypatch",
    "pre-commit",
    "pre-merge-commit",
    "prepare-commit-msg",
    "commit-msg",
    "post-commit",
    "pre-rebase",
    "post-checkout",
    "post-merge",
    "pre-push",
    "post-rewrite",
    "sendemail-validate",
    "post-index-change",
)
_HOOK = """#!/bin/sh
set -eu
hook_name="$(basename "$0")"
if [ "$hook_name" = pre-commit ]; then
    corridor scan --staged
fi
repo_hook="$(git rev-parse --path-format=absolute --git-common-dir)/hooks/$hook_name"
if [ -x "$repo_hook" ] && [ "$repo_hook" != "$0" ]; then
    exec "$repo_hook" "$@"
fi
"""


def corridor_commit_scanning_enabled() -> bool:
    return os.environ.get(CORRIDOR_COMMIT_SCANNING_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def corridor_hook_setup_command() -> str:
    encoded_hook = base64.b64encode(_HOOK.encode()).decode()
    dispatcher = f"{CORRIDOR_HOOKS_PATH}/open-swe-corridor-hook"
    links = "; ".join(
        f"ln -sf open-swe-corridor-hook {CORRIDOR_HOOKS_PATH}/{name}" for name in _HOOK_NAMES
    )
    return (
        "set -eu; "
        "command -v corridor >/dev/null || "
        "{ echo 'Corridor commit scanning is enabled but the corridor CLI is unavailable' >&2; "
        "exit 1; }; "
        'repo_hooks="$(git config --local --get core.hooksPath || true)"; '
        'if [ -n "$repo_hooks" ]; then '
        "echo 'Corridor commit scanning requires removing the repository-local hooks path' >&2; "
        "exit 1; fi; "
        'current="$(git config --global --get core.hooksPath || true)"; '
        f'if [ -n "$current" ] && [ "$current" != "{CORRIDOR_HOOKS_PATH}" ]; then '
        "echo 'Corridor commit scanning will not replace the existing global hooks path' >&2; "
        "exit 1; fi; "
        f"install -d -m 0700 {CORRIDOR_HOOKS_PATH}; "
        f"printf %s {encoded_hook} | base64 -d > {dispatcher}; "
        f"chmod 0700 {dispatcher}; "
        f"{links}; "
        f"git config --global core.hooksPath {CORRIDOR_HOOKS_PATH}"
    )


def corridor_hook_cleanup_command() -> str:
    return (
        'current="$(git config --global --get core.hooksPath || true)"; '
        f'if [ "$current" = "{CORRIDOR_HOOKS_PATH}" ]; then '
        "git config --global --unset core.hooksPath; "
        f"rm -rf {CORRIDOR_HOOKS_PATH}; fi"
    )


def validate_corridor_commit_scanning_config(sandbox_type: str) -> None:
    if not corridor_commit_scanning_enabled():
        return
    if sandbox_type != "langsmith":
        raise ValueError(f"{CORRIDOR_COMMIT_SCANNING_ENV} requires SANDBOX_TYPE=langsmith")
    if not corridor_token():
        raise ValueError(
            f"{CORRIDOR_COMMIT_SCANNING_ENV} requires CORRIDOR_API_KEY or a Corridor token"
        )
