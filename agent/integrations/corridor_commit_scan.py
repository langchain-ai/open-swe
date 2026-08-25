"""Corridor pre-commit scanning for managed sandboxes."""

import base64
import os

CORRIDOR_COMMIT_SCANNING_ENV = "CORRIDOR_COMMIT_SCANNING_ENABLED"
CORRIDOR_HOOKS_PATH = "/root/.config/open-swe/git-hooks"

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
repo_hook="$(git rev-parse --path-format=absolute --git-common-dir)/hooks/$hook_name"
if [ "$hook_name" = pre-commit ]; then
    if [ -x "$repo_hook" ] && [ "$repo_hook" != "$0" ]; then
        "$repo_hook" "$@"
    fi
    corridor scan --staged
elif [ -x "$repo_hook" ] && [ "$repo_hook" != "$0" ]; then
    exec "$repo_hook" "$@"
fi
"""


def corridor_hook_script() -> str:
    return _HOOK


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
    install = (
        f"install -d -m 0700 {CORRIDOR_HOOKS_PATH} && "
        f"printf %s {encoded_hook} | base64 -d > {dispatcher} && "
        f"chmod 0700 {dispatcher} && "
        f"{links} && "
        f"git config --global core.hooksPath {CORRIDOR_HOOKS_PATH}"
    )
    warning = "echo 'Warning: Corridor commit scanning could not be configured; continuing' >&2"
    return (
        f"if ! command -v corridor >/dev/null; then {warning}; else "
        'current="$(git config --global --get core.hooksPath || true)"; '
        f'if [ -n "$current" ] && [ "$current" != "{CORRIDOR_HOOKS_PATH}" ]; then '
        f"{warning}; elif ! ({install}); then {warning}; fi; fi"
    )


def corridor_hook_cleanup_command() -> str:
    return (
        'current="$(git config --global --get core.hooksPath || true)"; '
        f'if [ "$current" = "{CORRIDOR_HOOKS_PATH}" ]; then '
        "git config --global --unset core.hooksPath; "
        f"rm -rf {CORRIDOR_HOOKS_PATH}; fi"
    )
