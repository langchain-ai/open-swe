"""Provision isolated delivery workspaces before worker execution."""

from __future__ import annotations

import asyncio
import logging
import posixpath
import shlex
from collections.abc import Mapping
from typing import Any

from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)

WORKSPACE_PROVISION_TIMEOUT_SECONDS = 300


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _slug(value: str) -> str:
    cleaned: list[str] = []
    previous_dash = False
    for char in value.strip().lower():
        if char.isalnum():
            cleaned.append(char)
            previous_dash = False
        elif not previous_dash:
            cleaned.append("-")
            previous_dash = True
    return "".join(cleaned).strip("-") or "delivery-workspace"


def _repo(worker_input: Mapping[str, Any]) -> dict[str, str]:
    issue_context = _mapping(worker_input.get("issue_context"))
    repo = _mapping(issue_context.get("repository"))
    owner = _text(repo.get("owner"))
    name = _text(repo.get("name"))
    return {"owner": owner, "name": name} if owner and name else {}


def _worktree(worker_input: Mapping[str, Any]) -> dict[str, Any]:
    sandbox_profile = _mapping(worker_input.get("sandbox_profile"))
    return _mapping(sandbox_profile.get("worktree"))


def _branch(worker_input: Mapping[str, Any], worktree: Mapping[str, Any]) -> str:
    issue_context = _mapping(worker_input.get("issue_context"))
    return _text(worktree.get("branch")) or _text(issue_context.get("branch")) or "delivery/item"


def _base_branch(worker_input: Mapping[str, Any], worktree: Mapping[str, Any]) -> str:
    issue_context = _mapping(worker_input.get("issue_context"))
    return _text(worktree.get("base_branch")) or _text(issue_context.get("base_branch")) or "main"


def _workspace_path(
    worker_input: Mapping[str, Any],
    *,
    default_work_dir: str,
    branch: str,
) -> str:
    worktree = _worktree(worker_input)
    configured = _text(worktree.get("path"))
    if configured:
        cleaned = configured.rstrip("/")
        if cleaned and cleaned != ".":
            return cleaned
    return posixpath.join(default_work_dir.rstrip("/"), "worktrees", _slug(branch))


def _checkout_command(
    *,
    owner: str,
    repo: str,
    path: str,
    branch: str,
    base_branch: str,
) -> str:
    parent = posixpath.dirname(path.rstrip("/")) or "."
    full_name = f"{owner}/{repo}"
    q_parent = shlex.quote(parent)
    q_path = shlex.quote(path)
    q_full_name = shlex.quote(full_name)
    q_origin_url = shlex.quote(f"https://github.com/{full_name}.git")
    q_branch = shlex.quote(branch)
    q_base_branch = shlex.quote(base_branch)
    return "\n".join(
        [
            "set -e",
            f"mkdir -p {q_parent}",
            f"if [ -d {q_path}/.git ]; then",
            f"  cd {q_path}",
            f"  git remote set-url origin {q_origin_url} || true",
            f"  GH_TOKEN=dummy git fetch origin {q_base_branch} --quiet",
            "else",
            f"  if [ -e {q_path} ]; then echo 'workspace path exists and is not a git repo'; exit 66; fi",
            f"  GH_TOKEN=dummy gh repo clone {q_full_name} {q_path}",
            f"  cd {q_path}",
            f"  GH_TOKEN=dummy git fetch origin {q_base_branch} --quiet",
            "fi",
            f"git checkout --force -B {q_branch} origin/{q_base_branch}",
            f"git reset --hard origin/{q_base_branch} --quiet",
            "git clean -fdx --quiet",
            f'[ "$(git branch --show-current)" = {q_branch} ]',
            f'[ "$(git rev-parse --show-toplevel)" = {q_path} ]',
        ]
    )


async def provision_delivery_workspace(
    sandbox_backend: SandboxBackendProtocol,
    *,
    worker_input: Mapping[str, Any],
    default_work_dir: str,
) -> dict[str, Any]:
    repo = _repo(worker_input)
    if not repo:
        return {"status": "failed", "reason": "missing_repository"}

    worktree = _worktree(worker_input)
    branch = _branch(worker_input, worktree)
    base_branch = _base_branch(worker_input, worktree)
    path = _workspace_path(worker_input, default_work_dir=default_work_dir, branch=branch)
    command = _checkout_command(
        owner=repo["owner"],
        repo=repo["name"],
        path=path,
        branch=branch,
        base_branch=base_branch,
    )
    try:
        result = await asyncio.to_thread(
            sandbox_backend.execute,
            command,
            timeout=WORKSPACE_PROVISION_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to provision delivery workspace", exc_info=True)
        return {"status": "failed", "reason": "execute_error", "message": str(exc)}

    exit_code = getattr(result, "exit_code", None)
    if exit_code not in (0, None):
        return {
            "status": "failed",
            "reason": "checkout_failed",
            "exit_code": exit_code,
            "output": _text(getattr(result, "output", "")),
        }

    return {
        "status": "ready",
        "strategy": "sandbox_git_checkout",
        "repo": repo,
        "path": path,
        "branch": branch,
        "base_branch": base_branch,
    }
