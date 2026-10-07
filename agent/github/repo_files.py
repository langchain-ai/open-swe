"""Small configuration files Open SWE reads from a repository's ``.open-swe`` directory."""

import logging
import random
from collections import Counter
from datetime import timedelta
from fnmatch import fnmatchcase

import httpx2
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

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


class ReviewChannelRule(BaseModel):
    paths: list[str]
    channel: str = Field(min_length=1)


class ReviewFile(BaseModel):
    filename: str


class RepoSettings(BaseModel):
    """``.open-swe/settings.json``; unknown keys are ignored so the file can grow."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    review_channel: str = Field("", alias="reviewChannel")
    review_channel_rules: list[ReviewChannelRule] = Field(
        default_factory=list, alias="reviewChannelRules"
    )

    @property
    def review_channels(self) -> set[str]:
        configured = [self.review_channel, *(rule.channel for rule in self.review_channel_rules)]
        return {channel.strip() for channel in configured if channel.strip()}

    def channel_for_files(self, filenames: list[str]) -> str:
        counts: Counter[str] = Counter()
        for filename in filenames:
            channel = next(
                (
                    rule.channel.strip()
                    for rule in self.review_channel_rules
                    if any(fnmatchcase(filename, path) for path in rule.paths)
                ),
                "" if self.review_channel_rules else self.review_channel.strip(),
            )
            if channel:
                counts[channel] += 1
        if not counts:
            return "" if self.review_channel_rules else self.review_channel.strip()
        count = max(counts.values())
        return random.choice([candidate for candidate, total in counts.items() if total == count])

    async def channel_for_pr(self, owner: str, repo: str, number: int, *, token: str) -> str:
        if not self.review_channel_rules:
            return self.review_channel.strip()
        filenames: list[str] = []
        adapter = TypeAdapter(list[ReviewFile])
        async with github_client(token=token) as client:
            for page in range(1, 31):
                response = await github_request(
                    client,
                    "GET",
                    f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}/files",
                    params={"per_page": "100", "page": str(page)},
                )
                response.raise_for_status()
                files = adapter.validate_python(response.json())
                filenames.extend(file.filename for file in files)
                if len(files) < 100:
                    return self.channel_for_files(filenames)
        raise ValueError("Cannot route a pull request with an incomplete changed-file list")

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
