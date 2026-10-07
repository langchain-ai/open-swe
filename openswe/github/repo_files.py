"""Small configuration files Open SWE reads from a repository's ``.open-swe`` directory."""

import logging
import random
from collections import Counter
from datetime import timedelta
from fnmatch import fnmatchcase

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from openswe.github.http import (
    GitHubAppUnavailable,
    GitHubClient,
    RepoClient,
    RepoFileUnreadableError,
)
from openswe.github.pull_request_status import PullRequestClient

logger = logging.getLogger(__name__)

__all__ = ["RepoFileUnreadableError", "RepoSettings", "fetch_repo_file"]

SETTINGS_PATH = ".open-swe/settings.json"
SETTINGS_MAX_CHARS = 10_000
SETTINGS_FRESH_FOR = timedelta(minutes=30)
SETTINGS_MAX_AGE = timedelta(hours=24)
_ROUTED_FILE_PAGES = 30


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
    """``RepoClient.read_file`` for callers that still hold a raw token, or none."""
    if not owner or not repo:
        return None
    async with GitHubClient.connect(token=token) as github:
        return await github.repo(owner, repo).read_file(
            path, ref, max_chars=max_chars, strict=strict
        )


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

    async def channel_for_pr(self, pull: PullRequestClient) -> str:
        if not self.review_channel_rules:
            return self.review_channel.strip()
        files = TypeAdapter(list[ReviewFile]).validate_python(
            await pull.repo.pages(f"pulls/{pull.number}/files", max_pages=_ROUTED_FILE_PAGES)
        )
        if len(files) >= _ROUTED_FILE_PAGES * 100:
            raise ValueError("Cannot route a pull request with an incomplete changed-file list")
        return self.channel_for_files([file.filename for file in files])

    @classmethod
    async def fetch(
        cls, repo: RepoClient, *, ref: str | None = None, strict: bool = False
    ) -> RepoSettings:
        """The settings at ``ref``, or the default branch's when ``ref`` has none.

        With ``strict``, an unreadable or invalid file raises instead of reading as empty.
        """
        content = None
        if ref:
            content = await repo.read_file(
                SETTINGS_PATH, ref, max_chars=SETTINGS_MAX_CHARS, strict=strict
            )
        if content is None:
            content = await repo.read_file(
                SETTINGS_PATH, None, max_chars=SETTINGS_MAX_CHARS, strict=strict
            )
        if content is None:
            return cls()
        try:
            return cls.model_validate_json(content)
        except ValidationError as exc:
            logger.warning(
                "repository settings file is invalid; ignoring it",
                extra={"repository": repo.full_name},
                exc_info=True,
            )
            if strict:
                raise RepoFileUnreadableError(f"{SETTINGS_PATH} is invalid") from exc
            return cls()

    @classmethod
    async def cached(cls, owner: str, repo: str) -> RepoSettings:
        """The default branch's settings as the App reads them, served stale while they revalidate.

        Reads as empty when the App cannot reach the repository.
        """
        from langgraph_api.cache import swr

        async def load() -> RepoSettings:
            try:
                async with GitHubClient.as_app(owner, repo) as github:
                    return await cls.fetch(github.repo(owner, repo))
            except GitHubAppUnavailable:
                logger.warning(
                    "No GitHub App token to read repository settings",
                    extra={"repository": f"{owner}/{repo}"},
                )
                return cls()

        key = f"repo-settings:{owner}/{repo}".lower()
        result = await swr(
            key, load, fresh_for=SETTINGS_FRESH_FOR, max_age=SETTINGS_MAX_AGE, model=cls
        )
        return result.value
