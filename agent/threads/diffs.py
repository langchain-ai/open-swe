"""Recovering a thread's work as diffs: sandbox patch, working tree, branch."""

import base64
import json
import logging
import posixpath
from collections.abc import Mapping, Sequence
from functools import cache
from importlib import resources
from typing import Any

import httpx2
from fastapi import HTTPException

from agent.github.pull_request_diff import build_compare_diff_files, build_pr_diff_files
from agent.github.repositories import Repository
from agent.slack.client import parse_github_pr_url
from agent.thread_repos import thread_repositories
from agent.threads.access import (
    _authorized_thread,
    _github_token_for_login,
    _readable_thread_metadata,
)
from agent.threads.proxy import _PROXY_REQUEST_TIMEOUT
from agent.utils.json_types import thread_metadata

logger = logging.getLogger(__name__)

_RECOVERY_PATCH_LIMIT_BYTES = 25 * 1024 * 1024
_RECOVERY_PATCH_TIMEOUT_SECONDS = 120
_UNSAFE_REF_CHARACTERS = set(" ~^:?*[\\\x7f") | {chr(code) for code in range(32)}


async def create_sandbox(*args: Any, **kwargs: Any) -> Any:
    # deferred: pulls deepagents -> langchain_anthropic -> anthropic at import time
    from agent.sandboxes.providers.registry import create_sandbox as _create_sandbox

    return await _create_sandbox(*args, **kwargs)


@cache
def _recovery_patch_script() -> str:
    return (
        resources.files("agent.resources").joinpath("recovery_patch.py").read_text(encoding="utf-8")
    )


def _recovery_patch_filename(thread_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in {"-", "_", "."} else "-" for c in thread_id)
    return f"open-swe-{(safe or 'thread')[:80]}.patch"


def _response_output(result: Any) -> str:
    output = result.get("output") if isinstance(result, dict) else getattr(result, "output", "")
    return output if isinstance(output, str) else str(output or "")


def _response_exit_code(result: Any) -> int | None:
    value = (
        result.get("exit_code") if isinstance(result, dict) else getattr(result, "exit_code", None)
    )
    return value if isinstance(value, int) else None


def _download_content(result: Any) -> bytes | None:
    for attr in ("content", "data", "bytes"):
        value = result.get(attr) if isinstance(result, dict) else getattr(result, attr, None)
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode()
    file_data = (
        result.get("file_data") if isinstance(result, dict) else getattr(result, "file_data", None)
    )
    if isinstance(file_data, bytes):
        return file_data
    if isinstance(file_data, str):
        return file_data.encode()
    if isinstance(file_data, dict):
        for key in ("content", "data", "bytes"):
            value = file_data.get(key)
            if isinstance(value, bytes):
                return value
            if isinstance(value, str):
                return value.encode()
    return None


def _recovery_patch_command(
    metadata: Mapping[str, Any], repositories: Sequence[Repository], thread_id: str
) -> str:
    payload = {
        "repo_names": [repository.name for repository in repositories],
        "base_branch": metadata.get("base_branch")
        if isinstance(metadata.get("base_branch"), str)
        else "main",
        "thread_key": _recovery_patch_filename(thread_id).removesuffix(".patch"),
    }
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    script = _recovery_patch_script().replace("__PAYLOAD__", encoded)
    return f"python - <<'PY'\n{script}PY"


async def get_dashboard_thread_recovery_patch(
    thread_id: str, login: str, *, email: str | None = None
) -> tuple[bytes, str]:
    thread = await _authorized_thread(thread_id, login, email=email)
    metadata = thread_metadata(thread)
    sandbox_id = metadata.get("sandbox_id")
    if not isinstance(sandbox_id, str) or not sandbox_id:
        raise HTTPException(404, "thread has no recoverable sandbox")

    try:
        sandbox = await create_sandbox(sandbox_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not connect to sandbox %s for recovery", sandbox_id, exc_info=True)
        raise HTTPException(502, "could not connect to thread sandbox") from exc

    command = _recovery_patch_command(metadata, await thread_repositories(metadata), thread_id)
    try:
        result = await sandbox.aexecute(command, timeout=_RECOVERY_PATCH_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Recovery patch generation failed for %s", thread_id, exc_info=True)
        raise HTTPException(502, "failed to generate recovery patch") from exc

    output = _response_output(result).strip()
    try:
        payload = json.loads(output.splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        logger.debug("Invalid recovery patch response for %s: %s", thread_id, output)
        raise HTTPException(502, "failed to generate recovery patch") from exc

    if _response_exit_code(result) not in {0, None} or payload.get("ok") is not True:
        detail = payload.get("error") if isinstance(payload.get("error"), str) else None
        logger.debug("Recovery patch generation failed for %s: %s", thread_id, detail)
        raise HTTPException(502, detail or "failed to generate recovery patch")

    size = payload.get("size")
    if not isinstance(size, int):
        raise HTTPException(502, "failed to generate recovery patch")
    if size == 0:
        raise HTTPException(404, "thread has no recoverable changes")
    if size > _RECOVERY_PATCH_LIMIT_BYTES:
        raise HTTPException(413, "recovery patch is too large to download")

    patch_path = payload.get("path")
    if not isinstance(patch_path, str) or not patch_path.startswith("/tmp/"):
        raise HTTPException(502, "failed to generate recovery patch")

    try:
        downloads = await sandbox.adownload_files([patch_path])
    except Exception as exc:  # noqa: BLE001
        logger.debug("Recovery patch download failed for %s", thread_id, exc_info=True)
        raise HTTPException(502, "failed to download recovery patch") from exc
    if not downloads:
        raise HTTPException(502, "failed to download recovery patch")
    content = _download_content(downloads[0])
    if content is None:
        raise HTTPException(502, "failed to download recovery patch")
    return content, _recovery_patch_filename(thread_id)


def _missing_diff() -> dict[str, Any]:
    return {
        "status": "missing",
        "files": [],
        "truncated": False,
        "summary": {"files": 0, "additions": 0, "deletions": 0},
        "repos": [],
    }


def _prefixed_files(files: Any, prefix: str | None) -> list[dict[str, Any]]:
    """Diff files with every path moved under ``prefix``, so repositories stay apart."""
    entries = [dict(item) for item in files if isinstance(item, Mapping)]
    if not prefix:
        return entries
    for entry in entries:
        for key in ("path", "previousPath"):
            value = entry.get(key)
            if isinstance(value, str) and value:
                entry[key] = f"{prefix}/{value}"
    return entries


def _merged_status(statuses: Sequence[str]) -> str:
    """One status for several repositories: anything read beats nothing read."""
    if "ready" in statuses:
        return "ready"
    if "error" in statuses:
        return "error"
    return "missing"


async def get_dashboard_thread_working_tree_diff(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    """Return the sandbox's live working tree against HEAD, across every repository."""
    from agent.sandboxes.paths import resolve_sandbox_work_dir
    from agent.utils.turn_checkpoint import read_turn_diff

    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    sandbox_id = metadata.get("sandbox_id")
    if not isinstance(sandbox_id, str) or not sandbox_id:
        return _missing_diff()
    try:
        sandbox = await create_sandbox(sandbox_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Could not connect to sandbox %s for working tree diff", sandbox_id)
        raise HTTPException(503, "Could not connect to the workspace.") from exc
    work_dir = await resolve_sandbox_work_dir(sandbox)
    repositories = await thread_repositories(metadata)
    if not repositories:
        return {**await read_turn_diff(sandbox, work_dir, "HEAD", None), "repos": []}

    prefix_paths = len(repositories) > 1
    files: list[dict[str, Any]] = []
    summary = {"files": 0, "additions": 0, "deletions": 0}
    statuses: list[str] = []
    truncated = False
    per_repo: list[dict[str, Any]] = []
    for repository in repositories:
        diff = await read_turn_diff(
            sandbox,
            work_dir,
            "HEAD",
            None,
            repo_path=posixpath.join(work_dir, repository.name),
        )
        repo_summary = diff["summary"] if isinstance(diff.get("summary"), Mapping) else {}
        for key in summary:
            value = repo_summary.get(key)
            if isinstance(value, int):
                summary[key] += value
        files.extend(
            _prefixed_files(diff.get("files") or [], repository.name if prefix_paths else None)
        )
        statuses.append(str(diff.get("status") or "missing"))
        truncated = truncated or bool(diff.get("truncated"))
        per_repo.append(
            {
                "repoFullName": repository.full_name,
                "status": diff.get("status"),
                "truncated": bool(diff.get("truncated")),
                "summary": dict(repo_summary),
            }
        )

    return {
        "status": _merged_status(statuses),
        "files": files,
        "truncated": truncated,
        "summary": summary,
        "repos": per_repo,
    }


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


def _pull_request_repo(
    metadata: Mapping[str, Any], number: int, repositories: Sequence[Repository]
) -> str | None:
    """The repository the thread's pull request lives in."""
    pr_ref = parse_github_pr_url(str(metadata.get("pr_url") or ""))
    if pr_ref and pr_ref.number == number:
        return f"{pr_ref.owner}/{pr_ref.repo}"
    records = metadata.get("pull_requests")
    if isinstance(records, list):
        for item in records:
            if not isinstance(item, Mapping) or item.get("number") != number:
                continue
            full_name = item.get("repo_full_name")
            if isinstance(full_name, str) and full_name:
                return full_name
    return repositories[0].full_name if len(repositories) == 1 else None


async def get_dashboard_thread_branch_diff(
    thread_id: str, login: str, *, email: str | None = None
) -> dict[str, Any]:
    """Everything the thread's branch changes against its base.

    Served from GitHub rather than the sandbox, so it outlives the workspace.
    A thread with a pull request reads that PR; one without compares its branch
    in every repository it works in to the base it was cut from, which is the
    same three-dot range the PR would eventually show.
    """
    metadata = await _readable_thread_metadata(thread_id, login=login, email=email)
    repositories = await thread_repositories(metadata)
    pr_number = metadata.get("pr_number")
    pull_request: int | None = pr_number if isinstance(pr_number, int) else None
    pr_full_name = (
        _pull_request_repo(metadata, pull_request, repositories)
        if pull_request is not None
        else None
    )
    if pr_full_name is None:
        pull_request = None

    base_ref = _safe_git_ref(metadata.get("base_branch")) or "main"
    head_ref = _safe_git_ref(metadata.get("branch_name"))
    if pull_request is None:
        if not repositories:
            raise HTTPException(404, "thread has no repository")
        if head_ref == base_ref:
            raise HTTPException(404, "thread never branched off its base")

    token = await _github_token_for_login(login)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    prefix_paths = len(repositories) > 1
    compared: list[tuple[Repository | None, str, dict[str, Any]]] = []
    async with httpx2.AsyncClient(headers=headers, timeout=_PROXY_REQUEST_TIMEOUT) as client:
        if pull_request is not None and pr_full_name is not None:
            compared.append(
                (None, pr_full_name, await build_pr_diff_files(client, pr_full_name, pull_request))
            )
        elif head_ref is not None:
            for repository in repositories:
                try:
                    diff = await build_compare_diff_files(
                        client, repository.full_name, base_ref, head_ref
                    )
                except HTTPException as exc:
                    if exc.status_code != 404:
                        raise
                    logger.debug(
                        "Branch missing on GitHub for thread repository",
                        extra={
                            "diff_repo_full_name": repository.full_name,
                            "diff_head_ref": head_ref,
                        },
                    )
                    continue
                compared.append((repository, repository.full_name, diff))
            if not compared:
                raise HTTPException(404, "branch not found on GitHub")
        else:
            raise HTTPException(404, "thread has no branch")

    files: list[dict[str, Any]] = []
    truncated = False
    per_repo: list[dict[str, Any]] = []
    for repository, full_name, diff in compared:
        repo_files = _prefixed_files(
            diff["files"], repository.name if repository is not None and prefix_paths else None
        )
        files.extend(repo_files)
        truncated = truncated or bool(diff["truncated"])
        per_repo.append(
            {
                "repoFullName": full_name,
                "baseSha": diff["base_sha"],
                "headSha": diff["head_sha"],
                "truncated": bool(diff["truncated"]),
                "files": len(repo_files),
            }
        )

    single = compared[0][2] if len(compared) == 1 else None
    return {
        "prNumber": pull_request,
        "baseRef": base_ref,
        "headRef": head_ref,
        "baseSha": single["base_sha"] if single else None,
        "headSha": single["head_sha"] if single else None,
        "truncated": truncated,
        "files": files,
        "repos": per_repo,
    }
