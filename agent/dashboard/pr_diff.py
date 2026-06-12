"""Shared builder for full-content PR diffs.

Fetches a PR's changed files and their full original/modified contents so the
UI can render syntax-highlighted diffs with pierre's ``MultiFileDiff``. Used by
both the thread PR diff endpoint (user token) and the review diff endpoint
(App installation token).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import HTTPException

_GITHUB_API = "https://api.github.com"

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
        response = await client.get(
            f"{_GITHUB_API}/repos/{full_name}/contents/{path}",
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
    pull_response = await client.get(f"{_GITHUB_API}/repos/{full_name}/pulls/{pr_number}")
    if pull_response.status_code == 404:
        raise HTTPException(404, "pull request not found")
    if pull_response.status_code != 200:
        raise HTTPException(502, f"github API error ({pull_response.status_code})")
    pull = pull_response.json()
    base_sha = pull.get("base", {}).get("sha")
    head_sha = pull.get("head", {}).get("sha")
    if not isinstance(base_sha, str) or not isinstance(head_sha, str):
        raise HTTPException(502, "github API returned an unexpected pull request payload")

    files_response = await client.get(
        f"{_GITHUB_API}/repos/{full_name}/pulls/{pr_number}/files",
        params={"per_page": 100},
    )
    if files_response.status_code != 200:
        raise HTTPException(502, f"github API error ({files_response.status_code})")
    raw_files = files_response.json()
    if not isinstance(raw_files, list):
        raise HTTPException(502, "github API returned an unexpected files payload")

    truncated = len(raw_files) > PR_DIFF_MAX_FILES
    raw_files = raw_files[:PR_DIFF_MAX_FILES]

    semaphore = asyncio.Semaphore(PR_DIFF_FETCH_CONCURRENCY)

    async def build_entry(raw: dict[str, Any]) -> dict[str, Any] | None:
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
                client, semaphore, full_name, original_path, base_sha
            )
        if status != "removed":
            modified = await _fetch_file_at_ref(client, semaphore, full_name, path, head_sha)

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
        "base_sha": base_sha,
        "head_sha": head_sha,
        "truncated": truncated,
        "files": [entry for entry in entries if entry is not None],
    }
