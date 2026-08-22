"""What the dashboard reads out of a thread's sandbox, or about its pull request.

Three views of "what changed": the live working tree against HEAD, the diff of
one run (from the copy persisted after it finished, or the git checkpoint the
run left behind), and the branch against its base (from GitHub, as the user —
the PR's diff once one exists). Plus the PRs' live health, the whole
uncommitted worktree as a downloadable patch for a stuck thread, and the
sandbox id the cloud terminal attaches to.
"""

import logging
import posixpath
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from ...github.api import github_client
from ...github.refs import parse_github_pr_url
from ...sandboxes.recovery_patch import generate_recovery_patch
from ...settings.github_tokens import get_valid_access_token
from ..authz import get_owned_thread_metadata, get_readable_thread_metadata
from ..pr_diff import build_compare_diff_files, build_pr_diff_files
from ..pull_request_status import get_pull_request_statuses
from .proxy import PROXY_REQUEST_TIMEOUT
from .serialize import SANDBOX_CREATING_SENTINEL, metadata_repo

logger = logging.getLogger(__name__)


async def create_sandbox(*args: Any, **kwargs: Any) -> Any:
    # deferred: pulls deepagents -> langchain_anthropic -> anthropic at import time
    from ...sandboxes.providers import create_sandbox as _create_sandbox

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


async def _connect_sandbox(sandbox_id: str, purpose: str) -> Any | None:
    try:
        return await create_sandbox(sandbox_id)
    except Exception:  # noqa: BLE001
        # A diff is a best-effort view: an unreachable sandbox reads as "no diff"
        # rather than failing the page.
        logger.debug("Could not connect to sandbox %s for %s", sandbox_id, purpose, exc_info=True)
        return None


async def get_dashboard_thread_working_tree_diff(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    """The sandbox's live working tree against HEAD."""
    from ...sandboxes.paths import aresolve_sandbox_work_dir
    from ...sandboxes.turn_checkpoint import read_turn_diff

    metadata = await get_readable_thread_metadata(thread_id)
    sandbox_id = metadata.get("sandbox_id")
    if not isinstance(sandbox_id, str) or not sandbox_id:
        return _no_diff("missing")
    sandbox = await _connect_sandbox(sandbox_id, "working tree diff")
    if sandbox is None:
        return _no_diff("missing")
    work_dir = await aresolve_sandbox_work_dir(sandbox)
    repo_path = next(
        (
            entry["repo_path"]
            for entry in reversed(_checkpoints(metadata))
            if isinstance(entry.get("repo_path"), str)
        ),
        None,
    )
    if repo_path is None:
        _, repo_name, _ = metadata_repo(metadata)
        repo_path = posixpath.join(work_dir, repo_name) if repo_name else None
    return await read_turn_diff(sandbox, work_dir, "HEAD", None, repo_path=repo_path)


async def get_dashboard_thread_run_diff(
    thread_id: str,
    login: str,
    *,
    turn_key: str,
    max_files: int = 200,
    include_content: bool = True,
    email: str | None = None,
) -> dict[str, Any]:
    """The persisted diff of one completed run, with its git checkpoint as fallback."""
    from ...sandboxes.turn_checkpoint import read_turn_diff
    from ...settings.run_diffs import get_run_diff, project_run_diff

    metadata = await get_readable_thread_metadata(thread_id)
    stored = await get_run_diff(thread_id, turn_key)
    if stored is not None:
        return project_run_diff(stored, max_files=max_files, include_content=include_content)

    checkpoints = _checkpoints(metadata)
    index = next((i for i, entry in enumerate(checkpoints) if entry.get("key") == turn_key), -1)
    sandbox_id = metadata.get("sandbox_id")
    if index < 0 or not isinstance(sandbox_id, str) or not sandbox_id:
        return _no_diff("missing")

    checkpoint = checkpoints[index]
    plan_ref = checkpoint.get("plan_ref")
    if checkpoint.get("plan_mode") is True and (
        not isinstance(plan_ref, str) or plan_ref == checkpoint.get("ref")
    ):
        return _no_diff("ready")

    head = plan_ref if isinstance(plan_ref, str) else None
    if head is None and index + 1 < len(checkpoints):
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

    sandbox = await _connect_sandbox(sandbox_id, "run diff")
    if sandbox is None:
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


_UNSAFE_REF_CHARACTERS = set(" ~^:?*[\\\x7f") | {chr(code) for code in range(32)}


def _safe_git_ref(value: Any) -> str | None:
    """A branch name safe to place in a GitHub API path, or ``None``."""
    if not isinstance(value, str) or not value or len(value) > 200:
        return None
    if value.startswith("-") or value.startswith("/") or value.endswith("/"):
        return None
    if ".." in value or "@{" in value or value.endswith(".lock"):
        return None
    if any(character in _UNSAFE_REF_CHARACTERS for character in value):
        return None
    return value


async def get_dashboard_thread_branch_diff(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    """Everything the thread's branch changes against its base.

    Served from GitHub rather than the sandbox, so it outlives the workspace.
    A thread with a pull request reads that PR; one without compares its branch
    to the base it was cut from, which is the same three-dot range the PR would
    eventually show.
    """
    metadata = await get_readable_thread_metadata(thread_id)
    pr_number = metadata.get("pr_number")
    pr_ref = parse_github_pr_url(str(metadata.get("pr_url") or ""))
    _, _, full_name = metadata_repo(metadata)
    if pr_ref and pr_ref.number == pr_number:
        full_name = f"{pr_ref.owner}/{pr_ref.repo}"
    if not full_name:
        raise HTTPException(404, "thread has no repository")
    pull_request: int | None = pr_number if isinstance(pr_number, int) else None

    base_ref = _safe_git_ref(metadata.get("base_branch")) or "main"
    head_ref = _safe_git_ref(metadata.get("branch_name"))
    if pull_request is None and head_ref == base_ref:
        raise HTTPException(404, "thread never branched off its base")

    token = await _github_token_for_login(login)
    async with github_client(token=token, timeout=PROXY_REQUEST_TIMEOUT) as client:
        if pull_request is not None:
            diff = await build_pr_diff_files(client, full_name, pull_request)
        elif head_ref is not None:
            diff = await build_compare_diff_files(client, full_name, base_ref, head_ref)
        else:
            raise HTTPException(404, "thread has no branch")

    return {
        "prNumber": pull_request,
        "baseRef": base_ref,
        "headRef": head_ref,
        "baseSha": diff["base_sha"],
        "headSha": diff["head_sha"],
        "truncated": diff["truncated"],
        "files": diff["files"],
    }


async def get_dashboard_thread_pull_request_status(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    """Live GitHub health for every pull request the thread tracks."""
    metadata = await get_readable_thread_metadata(thread_id)
    records = metadata.get("pull_requests")
    tracked = list(records) if isinstance(records, list) else []
    if not tracked:
        pr_url = metadata.get("pr_url")
        pr_ref = parse_github_pr_url(pr_url) if isinstance(pr_url, str) else None
        if pr_ref:
            tracked = [{"repo_full_name": f"{pr_ref.owner}/{pr_ref.repo}", "number": pr_ref.number}]
    if not tracked:
        return {"pullRequests": []}
    token = await _github_token_for_login(login)
    return {"pullRequests": await get_pull_request_statuses(tracked, token)}
