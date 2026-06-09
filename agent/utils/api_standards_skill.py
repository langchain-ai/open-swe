"""Fetch the API-standards skill from the LangSmith Context Hub.

The reviewer applies this skill when a PR adds or changes APIs, so it can
verify the changes against the team's API best practices. The skill lives in
the Context Hub as a skill repo (default handle ``api-standards``); we pull its
``SKILL.md`` at run start and inject it into the reviewer's system prompt.

Best-effort: any failure (missing handle, no API key, SDK error) returns
``None`` and the reviewer runs without the supplement.
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

API_STANDARDS_SKILL_HANDLE = os.environ.get("API_STANDARDS_SKILL_HANDLE", "api-standards")


def _pull_api_standards_skill_sync(handle: str) -> str | None:
    from langsmith import Client as LangSmithClient

    client = LangSmithClient()
    skill = client.pull_skill(handle)
    files = getattr(skill, "files", None) or {}
    entry = files.get("SKILL.md")
    content = getattr(entry, "content", None) if entry is not None else None
    if isinstance(content, str) and content.strip():
        return content.strip()
    return None


async def fetch_api_standards_skill(handle: str | None = None) -> str | None:
    """Return the API-standards ``SKILL.md`` content, or ``None`` on any failure."""
    resolved = handle or API_STANDARDS_SKILL_HANDLE
    if not resolved:
        return None
    try:
        content = await asyncio.to_thread(_pull_api_standards_skill_sync, resolved)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to pull API-standards skill '%s'", resolved, exc_info=True)
        return None
    if content:
        logger.info(
            "Loaded API-standards skill '%s' (%d chars) into reviewer prompt",
            resolved,
            len(content),
        )
    return content
