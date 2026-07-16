"""Fetch ``AGENTS.md`` (or ``CLAUDE.md`` fallback) from a GitHub repo.

Used by the reviewer to deterministically load repo conventions into context
without the model having to clone the repo and read the file itself.
"""

from __future__ import annotations

import asyncio
import logging
import posixpath
from collections.abc import Iterable
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# Cap the inlined content. AGENTS.md / CLAUDE.md is meant to be a short
# conventions doc; anything larger is probably accidental and would bloat
# every reviewer prompt.
_MAX_AGENTS_MD_BYTES = 64 * 1024

# Filenames tried in order of preference. AGENTS.md is the cross-tool standard;
# CLAUDE.md is the legacy Anthropic-specific filename still used by many repos.
_AGENT_DOC_FILENAMES = ("AGENTS.md", "CLAUDE.md")
_SCOPED_FETCH_CONCURRENCY = 8


def applicable_agents_md_paths(changed_files: Iterable[str]) -> list[str]:
    """Return repo-relative ancestor ``AGENTS.md`` paths for changed files.

    The root file is loaded separately (with the ``CLAUDE.md`` fallback), so
    this only returns directory-scoped files. Paths are ordered from shallowest
    to deepest so later instructions can take precedence in the prompt.
    """
    candidates: set[str] = set()
    for raw_path in changed_files:
        path = posixpath.normpath(raw_path.strip())
        if not raw_path.strip() or path in {".", ".."} or path.startswith(("/", "../")):
            continue
        parts = path.split("/")
        for depth in range(1, len(parts)):
            candidates.add("/".join((*parts[:depth], "AGENTS.md")))
    return sorted(candidates, key=lambda path: (path.count("/"), path))


async def fetch_agents_md(
    owner: str,
    repo: str,
    ref: str,
    *,
    token: str | None,
    timeout: float = 10.0,
) -> str | None:
    """Fetch ``AGENTS.md`` (or ``CLAUDE.md`` fallback) at ``ref`` from ``owner/repo``.

    Returns the raw file contents of the first matching file, or ``None`` if no
    file is found, a fetch fails, or the file exceeds the size cap. Only a 404
    (file absent) triggers fallback to the next filename; any other condition
    (oversize, HTTP error, unexpected status) returns ``None`` immediately so
    the reviewer does not enforce stale rules from a secondary file.
    """
    if not owner or not repo or not ref:
        return None

    headers = {
        "Accept": "application/vnd.github.raw",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        for filename in _AGENT_DOC_FILENAMES:
            url = f"https://api.github.com/repos/{owner}/{repo}/contents/{filename}"
            try:
                response = await client.get(url, headers=headers, params={"ref": ref})
            except httpx.HTTPError:
                logger.exception("Failed to fetch %s from %s/%s@%s", filename, owner, repo, ref)
                return None

            if response.status_code == 404:
                continue
            if response.status_code != 200:
                logger.warning(
                    "Unexpected status %s fetching %s from %s/%s@%s",
                    response.status_code,
                    filename,
                    owner,
                    repo,
                    ref,
                )
                return None

            content = response.text
            if len(content.encode("utf-8")) > _MAX_AGENTS_MD_BYTES:
                logger.info(
                    "%s in %s/%s@%s exceeds %d bytes; skipping inline",
                    filename,
                    owner,
                    repo,
                    ref,
                    _MAX_AGENTS_MD_BYTES,
                )
                return None

            logger.info(
                "Loaded %s (%d chars) from %s/%s@%s",
                filename,
                len(content),
                owner,
                repo,
                ref,
            )
            return content

    return None


async def fetch_scoped_agents_md(
    owner: str,
    repo: str,
    ref: str,
    changed_files: Iterable[str],
    *,
    token: str | None,
    timeout: float = 10.0,
) -> dict[str, str]:
    """Fetch applicable directory-scoped ``AGENTS.md`` files at ``ref``.

    Each candidate is an ancestor of at least one changed file. Missing,
    oversized, or unavailable files are skipped independently so one bad path
    does not hide valid instructions in another changed subtree.
    """
    if not owner or not repo or not ref:
        return {}
    candidates = applicable_agents_md_paths(changed_files)
    if not candidates:
        return {}

    headers = {
        "Accept": "application/vnd.github.raw",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    semaphore = asyncio.Semaphore(_SCOPED_FETCH_CONCURRENCY)
    async with httpx.AsyncClient(timeout=timeout) as client:

        async def _fetch(path: str) -> tuple[str, str] | None:
            url_path = quote(path, safe="/")
            url = f"https://api.github.com/repos/{owner}/{repo}/contents/{url_path}"
            try:
                async with semaphore:
                    response = await client.get(url, headers=headers, params={"ref": ref})
            except httpx.HTTPError:
                logger.exception("Failed to fetch %s from %s/%s@%s", path, owner, repo, ref)
                return None
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                logger.warning(
                    "Unexpected status %s fetching %s from %s/%s@%s",
                    response.status_code,
                    path,
                    owner,
                    repo,
                    ref,
                )
                return None
            content = response.text
            if len(content.encode("utf-8")) > _MAX_AGENTS_MD_BYTES:
                logger.info(
                    "%s in %s/%s@%s exceeds %d bytes; skipping inline",
                    path,
                    owner,
                    repo,
                    ref,
                    _MAX_AGENTS_MD_BYTES,
                )
                return None
            return path, content

        fetched = await asyncio.gather(*(_fetch(path) for path in candidates))

    result: dict[str, str] = {}
    for item in fetched:
        if item is not None:
            path, content = item
            result[path] = content
    return result
