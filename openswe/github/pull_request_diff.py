"""Shared builders for GitHub diffs.

Fetches the changed files of a pull request — or of an arbitrary
``base...head`` comparison, for a branch that has no pull request — as per-file
git patches, optionally with their full original/modified contents so the UI
can render syntax-highlighted diffs with pierre's ``MultiFileDiff``. Used by
the thread branch diff endpoint (user token) and the review diff endpoint (App
installation token).
"""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx2
from fastapi import HTTPException

from openswe.github.http import GitHubError, RepoClient
from openswe.github.pull_request_status import PullRequestClient

logger = logging.getLogger(__name__)

# GitHub lists at most this many files for a pull request or comparison.
GITHUB_MAX_LISTED_FILES = 3000
PR_DIFF_MAX_FILE_BYTES = 1_000_000
PR_DIFF_FETCH_CONCURRENCY = 10
_FILES_PAGE_SIZE = 100


@contextmanager
def _github_answers(not_found: str) -> Iterator[None]:
    """GitHub's refusals as the errors the diff endpoints answer with."""
    try:
        yield
    except GitHubError as refused:
        status = refused.response.status_code
        if status == 404:
            raise HTTPException(404, not_found) from refused
        raise HTTPException(502, f"github API error ({status})") from refused
    except ValueError as exc:
        raise HTTPException(502, "github API returned an unexpected payload") from exc


async def _read_text(
    repo: RepoClient, semaphore: asyncio.Semaphore, path: str, ref: str
) -> str | None:
    """``path`` at ``ref``; ``None`` when it is absent, binary, oversized or unreadable."""
    async with semaphore:
        try:
            content = await repo.raw_file(path, ref)
        except httpx2.HTTPError:
            logger.warning(
                "diff file read failed",
                extra={"repo_full_name": repo.full_name, "path": path, "ref": ref},
                exc_info=True,
            )
            return None
    if content is None or len(content) > PR_DIFF_MAX_FILE_BYTES:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


async def resolve_merge_base(repo: RepoClient, base_ref: str, head_ref: str) -> str:
    """Where ``base...head`` starts: the "before" side of every file in its diff."""
    with _github_answers("revision not found on GitHub"):
        return await repo.merge_base(base_ref, head_ref)


async def fetch_file_versions(
    repo: RepoClient, path: str, original_path: str, base_ref: str, head_ref: str
) -> dict[str, str | None]:
    """Return one path's contents at both refs, for hydrating a partial diff."""
    for candidate in (path, original_path):
        if (
            not candidate
            or candidate.startswith("/")
            or "\\" in candidate
            or any(segment in (".", "..", "") for segment in candidate.split("/"))
        ):
            raise HTTPException(400, "invalid repository-relative file path")
    semaphore = asyncio.Semaphore(PR_DIFF_FETCH_CONCURRENCY)
    original, modified = await asyncio.gather(
        _read_text(repo, semaphore, original_path, base_ref),
        _read_text(repo, semaphore, path, head_ref),
    )
    return {"originalContent": original, "modifiedContent": modified}


def _git_patch(path: str, original_path: str, status: str, patch: str | None) -> str | None:
    """Wrap GitHub's hunks-only patch in the git file header pierre's parser needs."""
    header = f"diff --git a/{original_path} b/{path}\n"
    if original_path != path:
        header += f"rename from {original_path}\nrename to {path}\n"
    if patch is None:
        # GitHub omits the patch for binary blobs and for renames that changed
        # nothing; only the latter is renderable, as a header-only rename.
        return header if original_path != path else None
    old = "/dev/null" if status == "added" else f"a/{original_path}"
    new = "/dev/null" if status == "removed" else f"b/{path}"
    body = patch if patch.endswith("\n") else f"{patch}\n"
    return f"{header}--- {old}\n+++ {new}\n{body}"


def _file_entry(raw: dict[str, Any]) -> dict[str, Any] | None:
    """One listed file as a git patch; ``None`` when GitHub sent no path."""
    path = raw.get("filename")
    if not isinstance(path, str):
        return None
    status, previous, raw_patch = raw.get("status"), raw.get("previous_filename"), raw.get("patch")
    additions, deletions = raw.get("additions"), raw.get("deletions")
    status = status if isinstance(status, str) else "modified"
    previous = previous if isinstance(previous, str) else None
    patch = _git_patch(
        path, previous or path, status, raw_patch if isinstance(raw_patch, str) else None
    )
    return {
        "path": path,
        "previousPath": previous,
        "status": status,
        "additions": additions if isinstance(additions, int) else 0,
        "deletions": deletions if isinstance(deletions, int) else 0,
        "patch": patch,
        # GitHub omits the patch for binary and very large files, which leaves
        # nothing to render.
        "unrenderable": patch is None,
    }


def _patches(raw_files: list[dict[str, Any]], base_sha: str, head_sha: str) -> dict[str, Any]:
    entries = (_file_entry(raw) for raw in raw_files)
    return {
        "base_sha": base_sha,
        "head_sha": head_sha,
        "truncated": len(raw_files) >= GITHUB_MAX_LISTED_FILES,
        "files": [entry for entry in entries if entry is not None],
    }


async def _with_contents(repo: RepoClient, diff: dict[str, Any]) -> dict[str, Any]:
    """``diff`` with each file's full contents read at its ``base_sha`` and ``head_sha``.

    A blob that is binary, oversized or unreadable reads as ``None`` and marks
    its file ``unrenderable``.
    """
    semaphore = asyncio.Semaphore(PR_DIFF_FETCH_CONCURRENCY)

    async def hydrate(entry: dict[str, Any]) -> dict[str, Any]:
        original: str | None = ""
        modified: str | None = ""
        if entry["status"] != "added":
            original_path = entry["previousPath"] or entry["path"]
            original = await _read_text(repo, semaphore, original_path, diff["base_sha"])
        if entry["status"] != "removed":
            modified = await _read_text(repo, semaphore, entry["path"], diff["head_sha"])
        return {
            **entry,
            "originalContent": original,
            "modifiedContent": modified,
            "unrenderable": original is None or modified is None,
        }

    return {**diff, "files": await asyncio.gather(*map(hydrate, diff["files"]))}


async def _pull_files(pull: PullRequestClient) -> tuple[str, str, list[dict[str, Any]]]:
    """The pull's base branch tip, its head, and every file it changes."""
    with _github_answers("pull request not found"):
        payload = await pull.pull()
        base = payload.get("base")
        head = payload.get("head")
        base_sha = base.get("sha") if isinstance(base, dict) else None
        head_sha = head.get("sha") if isinstance(head, dict) else None
        if not isinstance(base_sha, str) or not isinstance(head_sha, str):
            raise ValueError("GitHub answered the pull request without its branches")
        changed = payload.get("changed_files")
        files = await pull.files(
            max_pages=GITHUB_MAX_LISTED_FILES // _FILES_PAGE_SIZE,
            count=changed if isinstance(changed, int) else None,
        )
    return base_sha, head_sha, files


async def pull_request_patches(pull: PullRequestClient) -> dict[str, Any]:
    """Return ``{base_sha, head_sha, truncated, files}`` for a PR, each file a git patch.

    ``base_sha`` is the base branch tip rather than the merge base the patches
    start from, which would cost a comparison; :func:`resolve_merge_base` finds
    that when a file's contents are wanted.
    """
    base_sha, head_sha, raw_files = await _pull_files(pull)
    return _patches(raw_files, base_sha, head_sha)


async def build_pr_diff_files(pull: PullRequestClient) -> dict[str, Any]:
    """Return ``{base_sha, head_sha, truncated, files}`` for a PR, with full contents.

    ``base_sha`` is the merge base, so commits landed on the base branch since
    the PR forked are not attributed to it.
    """
    base_tip, head_sha, raw_files = await _pull_files(pull)
    merge_base = await resolve_merge_base(pull.repo, base_tip, head_sha)
    return await _with_contents(pull.repo, _patches(raw_files, merge_base, head_sha))


async def build_compare_diff_files(
    repo: RepoClient, base_ref: str, head_ref: str
) -> dict[str, Any]:
    """Return ``{base_sha, head_sha, truncated, files}`` for ``base...head``, with full contents.

    Three-dot compare semantics: the base is the merge base of the two refs, so
    commits landed on the base branch since the head branch forked are not
    attributed to it. Same payload shape as :func:`build_pr_diff_files`.

    ``head_sha`` is the ref itself, not a commit: the compare payload's commit
    list is paginated, so its last entry is not reliably the branch tip. Blobs
    are read at the ref, which always resolves to the tip.
    """
    with _github_answers("branch not found on GitHub"):
        comparison = await repo.compare(base_ref, head_ref)
    return await _with_contents(repo, _patches(comparison.files, comparison.merge_base, head_ref))
