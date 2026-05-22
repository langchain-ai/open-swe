"""Fetch ``AGENTS.md`` from a GitHub repo so it can be inlined into prompts.

Used by the reviewer to deterministically load repo conventions into context
without the model having to clone the repo and read the file itself.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# Cap the inlined content. AGENTS.md is meant to be a short conventions doc;
# anything larger is probably accidental and would bloat every reviewer prompt.
_MAX_AGENTS_MD_BYTES = 64 * 1024


async def fetch_agents_md(
    owner: str,
    repo: str,
    ref: str,
    *,
    token: str | None,
    timeout: float = 10.0,
) -> str | None:
    """Fetch ``AGENTS.md`` at ``ref`` from ``owner/repo``.

    Returns the raw file contents, or ``None`` if the file is missing, the
    request fails, or the file exceeds the size cap.
    """
    if not owner or not repo or not ref:
        return None

    url = f"https://api.github.com/repos/{owner}/{repo}/contents/AGENTS.md"
    headers = {
        "Accept": "application/vnd.github.raw",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=headers, params={"ref": ref})
    except httpx.HTTPError:
        logger.exception("Failed to fetch AGENTS.md from %s/%s@%s", owner, repo, ref)
        return None

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        logger.warning(
            "Unexpected status %s fetching AGENTS.md from %s/%s@%s",
            response.status_code,
            owner,
            repo,
            ref,
        )
        return None

    content = response.text
    if len(content.encode("utf-8")) > _MAX_AGENTS_MD_BYTES:
        logger.info(
            "AGENTS.md in %s/%s@%s exceeds %d bytes; skipping inline",
            owner,
            repo,
            ref,
            _MAX_AGENTS_MD_BYTES,
        )
        return None
    return content
