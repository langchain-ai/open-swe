"""Compute normalized distance between opening and merged pull request patches."""

import asyncio
from collections.abc import Sequence

import httpx2

from agent.github.app import get_github_app_installation_token
from agent.github.http import github_client, github_request

_GITHUB_API = "https://api.github.com"
_MAX_PATCH_CHARACTERS = 2_000_000
_MAX_DISTANCE_STEPS = 100_000


def _patch_lines(files: object) -> list[str] | None:
    if not isinstance(files, list):
        return None
    lines: list[str] = []
    characters = 0
    for file in files:
        if not isinstance(file, dict):
            return None
        patch = file.get("patch")
        if patch is None:
            return None
        path = file.get("filename")
        if not isinstance(path, str) or not isinstance(patch, str):
            return None
        characters += len(patch)
        if characters > _MAX_PATCH_CHARACTERS:
            return None
        additions = file.get("additions")
        deletions = file.get("deletions")
        if type(additions) is not int or type(deletions) is not int:
            return None
        added = deleted = 0
        for line in patch.splitlines():
            if line.startswith("+"):
                added += 1
                lines.append(f"{path}\0{line}")
            elif line.startswith("-"):
                deleted += 1
                lines.append(f"{path}\0{line}")
        if (added, deleted) != (additions, deletions):
            return None
    return lines


def _insert_delete_distance(before: Sequence[str], after: Sequence[str]) -> int | None:
    n, m = len(before), len(after)
    if not n or not m:
        return n + m
    frontier = {1: 0}
    steps = 0
    for edits in range(n + m + 1):
        for diagonal in range(-edits, edits + 1, 2):
            steps += 1
            if steps > _MAX_DISTANCE_STEPS:
                return None
            if diagonal == -edits or (
                diagonal != edits
                and frontier.get(diagonal - 1, -1) < frontier.get(diagonal + 1, -1)
            ):
                x = frontier.get(diagonal + 1, 0)
            else:
                x = frontier.get(diagonal - 1, 0) + 1
            y = x - diagonal
            while x < n and y < m and before[x] == after[y]:
                steps += 1
                if steps > _MAX_DISTANCE_STEPS:
                    return None
                x += 1
                y += 1
            frontier[diagonal] = x
            if x >= n and y >= m:
                return edits
    return n + m


async def _compare_files(
    client: httpx2.AsyncClient, owner: str, repo: str, base: str, head: str
) -> object:
    response = await github_request(
        client,
        "GET",
        f"{_GITHUB_API}/repos/{owner}/{repo}/compare/{base}...{head}",
        max_retries=1,
    )
    if response.status_code != 200:
        return None
    payload = response.json()
    if not isinstance(payload, dict):
        return None
    files = payload.get("files")
    if isinstance(files, list) and len(files) >= 300:
        return None
    return files


async def post_open_distance_basis_points(
    *,
    owner: str,
    repo: str,
    opening_base_sha: str,
    opening_head_sha: str,
    final_base_sha: str,
    final_head_sha: str,
) -> int | None:
    """Return normalized line edit distance in basis points, or None when unavailable."""
    if not all((opening_base_sha, opening_head_sha, final_base_sha, final_head_sha)):
        return None
    token = await get_github_app_installation_token(repositories=[repo], log_errors=False)
    if not token:
        return None
    async with github_client(token=token) as client:
        opening_files = await _compare_files(
            client, owner, repo, opening_base_sha, opening_head_sha
        )
        final_files = await _compare_files(client, owner, repo, final_base_sha, final_head_sha)
    opening = await asyncio.to_thread(_patch_lines, opening_files)
    final = await asyncio.to_thread(_patch_lines, final_files)
    if opening is None or final is None:
        return None
    total = len(opening) + len(final)
    if total == 0:
        return 0
    edits = await asyncio.to_thread(_insert_delete_distance, opening, final)
    if edits is None:
        return None
    return round(10_000 * edits / total)
