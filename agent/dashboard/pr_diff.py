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

import httpx
from fastapi import HTTPException

from ..github.api import GITHUB_RAW_JSON_ACCEPT, github_request, github_url

PR_DIFF_MAX_FILES = 50
PR_DIFF_MAX_FILE_BYTES = 200_000
PR_DIFF_FETCH_CONCURRENCY = 5


async def _fetch_file_at_ref(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    full_name: str,
    path: str,
    ref: str,
) -> str | None:
    async with semaphore:
        response = await github_request(
            client,
            "GET",
            github_url(f"/repos/{full_name}/contents/{quote(path, safe='/')}"),
            params={"ref": ref},
            headers={"Accept": GITHUB_RAW_JSON_ACCEPT},
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


async def build_pr_diff_files(
    client: httpx.AsyncClient,
    full_name: str,
    pr_number: int,
) -> dict[str, Any]:
    """Return ``{base_sha, head_sha, truncated, files}`` for a PR.

    Each file carries full ``originalContent``/``modifiedContent`` (or ``None``
    for binary/oversized blobs, flagged via ``unrenderable``). ``client`` must
    already be configured with auth headers.
    """
    pull_response = await github_request(
        client, "GET", github_url(f"/repos/{full_name}/pulls/{pr_number}")
    )
    if pull_response.status_code == 404:
        raise HTTPException(404, "pull request not found")
    if pull_response.status_code != 200:
        raise HTTPException(502, f"github API error ({pull_response.status_code})")
    pull = pull_response.json()
    base_sha = pull.get("base", {}).get("sha")
    head_sha = pull.get("head", {}).get("sha")
    if not isinstance(base_sha, str) or not isinstance(head_sha, str):
        raise HTTPException(502, "github API returned an unexpected pull request payload")

    files_response = await github_request(
        client,
        "GET",
        github_url(f"/repos/{full_name}/pulls/{pr_number}/files"),
        params={"per_page": 100},
    )
    if files_response.status_code != 200:
        raise HTTPException(502, f"github API error ({files_response.status_code})")
    raw_files = files_response.json()
    if not isinstance(raw_files, list):
        raise HTTPException(502, "github API returned an unexpected files payload")

    return await _build_diff_files(client, full_name, raw_files, base_sha, head_sha)


async def build_compare_diff_files(
    client: httpx.AsyncClient,
    full_name: str,
    base_ref: str,
    head_ref: str,
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
    base = quote(base_ref, safe="")
    head = quote(head_ref, safe="")
    response = await github_request(
        client, "GET", github_url(f"/repos/{full_name}/compare/{base}...{head}")
    )
    if response.status_code == 404:
        raise HTTPException(404, "branch not found on GitHub")
    if response.status_code != 200:
        raise HTTPException(502, f"github API error ({response.status_code})")
    comparison = response.json()
    if not isinstance(comparison, dict):
        raise HTTPException(502, "github API returned an unexpected compare payload")
    merge_base = comparison.get("merge_base_commit")
    base_sha = merge_base.get("sha") if isinstance(merge_base, dict) else None
    if not isinstance(base_sha, str):
        raise HTTPException(502, "github API returned an unexpected compare payload")

    raw_files = comparison.get("files")
    if raw_files is None:
        raw_files = []
    if not isinstance(raw_files, list):
        raise HTTPException(502, "github API returned an unexpected files payload")

    return await _build_diff_files(client, full_name, raw_files, base_sha, head_ref)


async def _build_diff_files(
    client: httpx.AsyncClient,
    full_name: str,
    raw_files: list[Any],
    base_ref: str,
    head_ref: str,
) -> dict[str, Any]:
    """Build file entries by reading each blob at ``base_ref`` and ``head_ref``."""
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
        if status != "added":
            original = await _fetch_file_at_ref(
                client, semaphore, full_name, original_path, base_ref
            )
        if status != "removed":
            modified = await _fetch_file_at_ref(client, semaphore, full_name, path, head_ref)

        return {
            "path": path,
            "previousPath": previous if isinstance(previous, str) else None,
            "status": status,
            "additions": raw.get("additions") if isinstance(raw.get("additions"), int) else 0,
            "deletions": raw.get("deletions") if isinstance(raw.get("deletions"), int) else 0,
            "originalContent": original,
            "modifiedContent": modified,
            # Binary or oversized blobs come back as None — the client renders a
            # placeholder instead of file contents.
            "unrenderable": original is None or modified is None,
        }

    entries = await asyncio.gather(*(build_entry(raw) for raw in raw_files))

    return {
        "base_sha": base_ref,
        "head_sha": head_ref,
        "truncated": truncated,
        "files": [entry for entry in entries if entry is not None],
    }
