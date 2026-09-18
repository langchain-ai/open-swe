"""Shared builders for full-content GitHub diffs.

Fetches the changed files of a pull request — or of an arbitrary
``base...head`` comparison, for a branch that has no pull request — together
with their full original/modified contents, so the UI can render
syntax-highlighted diffs with pierre's ``MultiFileDiff``. Used by the thread
branch diff endpoint (user token) and the review diff endpoint (App
installation token).
"""

import asyncio
from typing import Any
from urllib.parse import quote

import httpx2
from fastapi import HTTPException

_GITHUB_API = "https://api.github.com"

PR_DIFF_MAX_FILES = 50
PR_DIFF_MAX_FILE_BYTES = 1_000_000
PR_DIFF_FETCH_CONCURRENCY = 5


async def _fetch_file_at_ref(
    client: httpx2.AsyncClient,
    semaphore: asyncio.Semaphore,
    full_name: str,
    path: str,
    ref: str,
) -> str | None:
    async with semaphore:
        response = await client.get(
            f"{_GITHUB_API}/repos/{full_name}/contents/{quote(path, safe='/')}",
            params={"ref": ref},
            headers={"Accept": "application/vnd.github.raw+json"},
        )
    if response.status_code == 404:
        return ""
    if response.status_code != 200:
        return None
    if len(response.content) > PR_DIFF_MAX_FILE_BYTES:
        return None
    try:
        return response.content.decode("utf-8")
    except UnicodeDecodeError:
        return None


async def fetch_file_versions(
    client: httpx2.AsyncClient,
    full_name: str,
    path: str,
    original_path: str,
    base_ref: str,
    head_ref: str,
) -> dict[str, str | None]:
    """Return one path's contents at both refs, for hydrating a partial diff."""
    semaphore = asyncio.Semaphore(PR_DIFF_FETCH_CONCURRENCY)
    original, modified = await asyncio.gather(
        _fetch_file_at_ref(client, semaphore, full_name, original_path, base_ref),
        _fetch_file_at_ref(client, semaphore, full_name, path, head_ref),
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


async def _fetch_comparison(
    client: httpx2.AsyncClient, full_name: str, base_ref: str, head_ref: str
) -> dict[str, Any]:
    """Fetch ``base...head``; refs are fully escaped so neither can widen the path."""
    base = quote(base_ref, safe="")
    head = quote(head_ref, safe="")
    response = await client.get(f"{_GITHUB_API}/repos/{full_name}/compare/{base}...{head}")
    if response.status_code == 404:
        raise HTTPException(404, "branch not found on GitHub")
    if response.status_code != 200:
        raise HTTPException(502, f"github API error ({response.status_code})")
    comparison = response.json()
    if not isinstance(comparison, dict):
        raise HTTPException(502, "github API returned an unexpected compare payload")
    return comparison


def _merge_base_sha(comparison: dict[str, Any]) -> str:
    merge_base = comparison.get("merge_base_commit")
    base_sha = merge_base.get("sha") if isinstance(merge_base, dict) else None
    if not isinstance(base_sha, str):
        raise HTTPException(502, "github API returned an unexpected compare payload")
    return base_sha


async def _pull_branch_shas(
    client: httpx2.AsyncClient, full_name: str, pr_number: int
) -> tuple[str, str]:
    pull_response = await client.get(f"{_GITHUB_API}/repos/{full_name}/pulls/{pr_number}")
    if pull_response.status_code == 404:
        raise HTTPException(404, "pull request not found")
    if pull_response.status_code != 200:
        raise HTTPException(502, f"github API error ({pull_response.status_code})")
    pull = pull_response.json()
    base = pull.get("base") if isinstance(pull, dict) else None
    head = pull.get("head") if isinstance(pull, dict) else None
    base_sha = base.get("sha") if isinstance(base, dict) else None
    head_sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(base_sha, str) or not isinstance(head_sha, str):
        raise HTTPException(502, "github API returned an unexpected pull request payload")
    return base_sha, head_sha


async def pull_request_diff_refs(
    client: httpx2.AsyncClient, full_name: str, pr_number: int
) -> tuple[str, str]:
    """Return the PR's ``(merge_base_sha, head_sha)`` — the refs its diff spans."""
    base_sha, head_sha = await _pull_branch_shas(client, full_name, pr_number)
    comparison = await _fetch_comparison(client, full_name, base_sha, head_sha)
    return _merge_base_sha(comparison), head_sha


async def build_pr_diff_files(
    client: httpx2.AsyncClient,
    full_name: str,
    pr_number: int,
    *,
    with_contents: bool = True,
) -> dict[str, Any]:
    """Return ``{base_sha, head_sha, truncated, files}`` for a PR.

    With ``with_contents``, each file carries full ``originalContent``/
    ``modifiedContent`` (or ``None`` for binary/oversized blobs, flagged via
    ``unrenderable``); without it only ``patch`` is populated and the client
    hydrates on demand. ``client`` must already be configured with auth headers.
    """
    base_sha, head_sha = await _pull_branch_shas(client, full_name, pr_number)
    # ``base.sha`` is the base branch's tip, not the merge base. Reading original
    # blobs there would attribute every commit landed on the base branch since
    # the fork to this PR, as deletions.
    return await build_compare_diff_files(
        client, full_name, base_sha, head_sha, with_contents=with_contents
    )


async def build_compare_diff_files(
    client: httpx2.AsyncClient,
    full_name: str,
    base_ref: str,
    head_ref: str,
    *,
    with_contents: bool = True,
) -> dict[str, Any]:
    """Return ``{base_sha, head_sha, truncated, files}`` for ``base...head``.

    Three-dot compare semantics: the base is the merge base of the two refs, so
    commits landed on the base branch since the head branch forked are not
    attributed to it. Same payload shape as :func:`build_pr_diff_files`; refs
    are fully escaped, so a ref can never widen the request path.

    ``head_sha`` is the ref itself, not a commit: the compare payload's commit
    list is paginated, so its last entry is not reliably the branch tip. Blobs
    are read at the ref, which always resolves to the tip.
    """
    comparison = await _fetch_comparison(client, full_name, base_ref, head_ref)
    base_sha = _merge_base_sha(comparison)

    raw_files = comparison.get("files")
    if raw_files is None:
        raw_files = []
    if not isinstance(raw_files, list):
        raise HTTPException(502, "github API returned an unexpected files payload")

    return await _build_diff_files(
        client, full_name, raw_files, base_sha, head_ref, with_contents=with_contents
    )


async def _build_diff_files(
    client: httpx2.AsyncClient,
    full_name: str,
    raw_files: list[Any],
    base_ref: str,
    head_ref: str,
    *,
    with_contents: bool,
) -> dict[str, Any]:
    """Build file entries, optionally reading each blob at ``base_ref``/``head_ref``."""
    truncated = len(raw_files) > PR_DIFF_MAX_FILES
    raw_files = raw_files[:PR_DIFF_MAX_FILES]

    semaphore = asyncio.Semaphore(PR_DIFF_FETCH_CONCURRENCY)

    async def build_entry(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        path = raw.get("filename")
        if not isinstance(path, str):
            return None
        status = raw.get("status") if isinstance(raw.get("status"), str) else "modified"
        previous = raw.get("previous_filename")
        original_path = previous if isinstance(previous, str) else path

        original: str | None = ""
        modified: str | None = ""
        if with_contents:
            if status != "added":
                original = await _fetch_file_at_ref(
                    client, semaphore, full_name, original_path, base_ref
                )
            if status != "removed":
                modified = await _fetch_file_at_ref(client, semaphore, full_name, path, head_ref)

        raw_patch = raw.get("patch") if isinstance(raw.get("patch"), str) else None
        patch = _git_patch(path, original_path, status, raw_patch)
        return {
            "path": path,
            "previousPath": previous if isinstance(previous, str) else None,
            "status": status,
            "additions": raw.get("additions") if isinstance(raw.get("additions"), int) else 0,
            "deletions": raw.get("deletions") if isinstance(raw.get("deletions"), int) else 0,
            "originalContent": original,
            "modifiedContent": modified,
            "patch": patch,
            # GitHub omits the patch for binary and very large files; without
            # contents to fall back on there is nothing to render.
            "unrenderable": (original is None or modified is None)
            if with_contents
            else patch is None,
        }

    entries = await asyncio.gather(*(build_entry(raw) for raw in raw_files))

    return {
        "base_sha": base_ref,
        "head_sha": head_ref,
        "truncated": truncated,
        "files": [entry for entry in entries if entry is not None],
    }
