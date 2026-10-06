"""Small configuration files Open SWE reads from a repository's ``.open-swe`` directory."""

import logging
from datetime import timedelta

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.github.http import GITHUB_API_BASE, github_client, github_request

logger = logging.getLogger(__name__)


class RepoFileUnreadableError(RuntimeError):
    """A repository file could not be read reliably."""


SETTINGS_PATH = ".open-swe/settings.json"
SETTINGS_MAX_CHARS = 10_000
SETTINGS_FRESH_FOR = timedelta(minutes=30)
SETTINGS_MAX_AGE = timedelta(hours=24)


async def fetch_repo_file(
    owner: str,
    repo: str,
    path: str,
    ref: str | None,
    *,
    token: str | None,
    max_chars: int,
    strict: bool = False,
) -> str | None:
    """``path`` at ``ref`` (the default branch when ``None``).

    With ``strict``, only absence returns ``None``; unreadable files raise.
    """
    if not owner or not repo:
        return None
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}"
    extra = {"repository": f"{owner}/{repo}", "ref": ref or "", "path": path}
    try:
        async with github_client(
            token=token, headers={"Accept": "application/vnd.github.raw"}
        ) as client:
            response = await github_request(
                client, "GET", url, params={"ref": ref} if ref else None
            )
    except httpx2.HTTPError:
        logger.exception("repository file fetch failed", extra=extra)
        if strict:
            raise RepoFileUnreadableError(f"Could not read {path}") from None
        return None
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        logger.warning(
            "repository file fetch returned an unexpected status",
            extra={**extra, "status_code": response.status_code},
        )
        if strict:
            raise RepoFileUnreadableError(f"Could not read {path}: HTTP {response.status_code}")
        return None
    content = response.text.strip()
    if len(content) > max_chars:
        logger.warning(
            "repository file exceeds the size cap; ignoring it",
            extra={**extra, "chars": len(content), "max_chars": max_chars},
        )
        if strict:
            raise RepoFileUnreadableError(f"Repository file {path} exceeds the size cap")
        return None
    return content if strict else content or None


class RepoSettings(BaseModel):
    """``.open-swe/settings.json``; unknown keys are ignored so the file can grow."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    review_channel: str = Field("", alias="reviewChannel")

    @classmethod
    async def fetch(
        cls, owner: str, repo: str, *, token: str | None, ref: str | None = None
    ) -> RepoSettings:
        """The settings at ``ref``, or the default branch's when ``ref`` has none."""
        content = None
        if ref:
            content = await fetch_repo_file(
                owner, repo, SETTINGS_PATH, ref, token=token, max_chars=SETTINGS_MAX_CHARS
            )
        if content is None:
            content = await fetch_repo_file(
                owner, repo, SETTINGS_PATH, None, token=token, max_chars=SETTINGS_MAX_CHARS
            )
        if content is None:
            return cls()
        try:
            return cls.model_validate_json(content)
        except ValidationError:
            logger.warning(
                "repository settings file is invalid; ignoring it",
                extra={"repository": f"{owner}/{repo}"},
                exc_info=True,
            )
            return cls()

    @classmethod
    async def cached(cls, owner: str, repo: str, *, token: str | None) -> RepoSettings:
        """The default branch's settings, served stale while they revalidate."""
        from langgraph_api.cache import swr

        async def load() -> RepoSettings:
            return await cls.fetch(owner, repo, token=token)

        key = f"repo-settings:{owner}/{repo}".lower()
        result = await swr(
            key, load, fresh_for=SETTINGS_FRESH_FOR, max_age=SETTINGS_MAX_AGE, model=cls
        )
        return result.value
