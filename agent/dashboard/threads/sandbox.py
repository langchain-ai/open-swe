"""What the dashboard reads out of a thread's sandbox, or about its pull request.

Three views of "what changed": the diff for one turn (from a git checkpoint the
run left behind, or the copy persisted after it finished), the diff of the PR
the thread opened (from GitHub, as the user), and — when a thread is stuck — the
whole uncommitted worktree as a downloadable patch. Plus the sandbox id the
cloud terminal attaches to.
"""

import logging
import posixpath
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from ...settings.github_tokens import get_valid_access_token
from ...utils.github_http import github_client
from ...utils.github_refs import parse_github_pr_url
from ...utils.recovery_patch import generate_recovery_patch
from ..authz import get_owned_thread_metadata, get_readable_thread_metadata
from ..pr_diff import build_pr_diff_files
from .proxy import PROXY_REQUEST_TIMEOUT
from .serialize import SANDBOX_CREATING_SENTINEL, metadata_repo

logger = logging.getLogger(__name__)


async def create_sandbox(*args: Any, **kwargs: Any) -> Any:
    # deferred: pulls deepagents -> langchain_anthropic -> anthropic at import time
    from ...utils.sandbox import create_sandbox as _create_sandbox

    return await _create_sandbox(*args, **kwargs)


def _no_diff(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "files": [],
        "truncated": False,
        "summary": {"files": 0, "additions": 0, "deletions": 0},
    }


async def get_dashboard_terminal_sandbox(
    thread_id: str, login: str, *, email: str | None = None
) -> tuple[str, str | None]:
    metadata = await get_owned_thread_metadata(thread_id, login, email=email)
    sandbox_id = metadata.get("sandbox_id")
    if not isinstance(sandbox_id, str) or not sandbox_id or sandbox_id == SANDBOX_CREATING_SENTINEL:
        raise HTTPException(404, "thread sandbox is not ready")
    repo_name = metadata.get("repo_name")
    if not isinstance(repo_name, str) or posixpath.basename(repo_name) != repo_name:
        repo_name = None
    return sandbox_id, repo_name


async def get_dashboard_thread_recovery_patch(
    thread_id: str, login: str, *, email: str | None = None
) -> tuple[bytes, str]:
    metadata = await get_owned_thread_metadata(thread_id, login, email=email)
    sandbox_id = metadata.get("sandbox_id")
    if not isinstance(sandbox_id, str) or not sandbox_id:
        raise HTTPException(404, "thread has no recoverable sandbox")

    try:
        sandbox = await create_sandbox(sandbox_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not connect to sandbox %s for recovery", sandbox_id, exc_info=True)
        raise HTTPException(502, "could not connect to thread sandbox") from exc

    return await generate_recovery_patch(sandbox, metadata, thread_id)


def _checkpoints(metadata: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    checkpoints = metadata.get("turn_checkpoints")
    return [
        entry
        for entry in (checkpoints if isinstance(checkpoints, list) else [])
        if isinstance(entry, Mapping) and isinstance(entry.get("ref"), str)
    ]


async def get_dashboard_thread_turn_diff(
    thread_id: str,
    login: str,
    *,
    turn_key: str | None = None,
    max_files: int = 200,
    include_content: bool = True,
    email: str | None = None,
) -> dict[str, Any]:
    """Return a persisted run diff, with sandbox checkpoints as a legacy fallback."""
    from ...utils.turn_checkpoint import read_turn_diff
    from ..run_diffs import THREAD_DIFF_KEY, get_run_diff, project_run_diff

    metadata = await get_readable_thread_metadata(thread_id)
    checkpoints = _checkpoints(metadata)
    index = (
        next((i for i, entry in enumerate(checkpoints) if entry.get("key") == turn_key), -1)
        if turn_key is not None
        else 0
    )
    sandbox_id = metadata.get("sandbox_id")
    if index < 0 or not checkpoints or not isinstance(sandbox_id, str) or not sandbox_id:
        return _no_diff("missing")

    checkpoint = checkpoints[index]
    stored = await get_run_diff(thread_id, turn_key if turn_key is not None else THREAD_DIFF_KEY)
    if stored is not None:
        return project_run_diff(stored, max_files=max_files, include_content=include_content)

    plan_ref = checkpoint.get("plan_ref")
    if (
        turn_key is not None
        and checkpoint.get("plan_mode") is True
        and (not isinstance(plan_ref, str) or plan_ref == checkpoint.get("ref"))
    ):
        return _no_diff("ready")

    head = plan_ref if turn_key is not None and isinstance(plan_ref, str) else None
    if head is None and turn_key and index + 1 < len(checkpoints):
        next_checkpoint = checkpoints[index + 1]
        repo_path = checkpoint.get("repo_path")
        next_repo_path = next_checkpoint.get("repo_path")
        if (
            isinstance(repo_path, str)
            and isinstance(next_repo_path, str)
            and repo_path != next_repo_path
        ):
            return _no_diff("missing")
        head = next_checkpoint["ref"]

    try:
        sandbox = await create_sandbox(sandbox_id)
    except Exception:  # noqa: BLE001
        logger.debug("Could not connect to sandbox %s for turn diff", sandbox_id, exc_info=True)
        return _no_diff("missing")

    repo_path = checkpoint.get("repo_path")
    return await read_turn_diff(
        sandbox,
        None,
        str(checkpoint["ref"]),
        head,
        max_files=max_files,
        include_content=include_content,
        repo_path=repo_path if isinstance(repo_path, str) else None,
    )


# No app-installation-token fallback: PR file contents must be fetched with
# the user's own credential so GitHub enforces their current repo access.
async def _github_token_for_login(login: str) -> str:
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")
    return token


async def get_dashboard_thread_pr_diff(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    metadata = await get_readable_thread_metadata(thread_id)
    pr_number = metadata.get("pr_number")
    pr_ref = parse_github_pr_url(str(metadata.get("pr_url") or ""))
    _, _, full_name = metadata_repo(metadata)
    if pr_ref and pr_ref.number == pr_number:
        full_name = f"{pr_ref.owner}/{pr_ref.repo}"
    if not isinstance(pr_number, int) or not full_name:
        raise HTTPException(404, "thread has no pull request")

    token = await _github_token_for_login(login)
    async with github_client(token=token, timeout=PROXY_REQUEST_TIMEOUT) as client:
        diff = await build_pr_diff_files(client, full_name, pr_number)

    return {
        "prNumber": pr_number,
        "baseSha": diff["base_sha"],
        "headSha": diff["head_sha"],
        "truncated": diff["truncated"],
        "files": diff["files"],
    }
