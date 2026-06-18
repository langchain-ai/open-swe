"""Fetch ``AGENTS.md`` (or ``CLAUDE.md`` fallback) from a GitHub repo.

Used by the reviewer to deterministically load repo conventions into context
without the model having to clone the repo and read the file itself.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# Cap the inlined content. AGENTS.md / CLAUDE.md is meant to be a short
# conventions doc; anything larger is probably accidental and would bloat
# every reviewer prompt.
_MAX_AGENTS_MD_BYTES = 64 * 1024

# Filenames tried in order of preference. AGENTS.md is the cross-tool standard;
# CLAUDE.md is the legacy Anthropic-specific filename still used by many repos.
_AGENT_DOC_FILENAMES = ("AGENTS.md", "CLAUDE.md")


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
    file is found, all requests fail, or the file exceeds the size cap.
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
                continue

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
                continue

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
                continue

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
