"""Small configuration files Open SWE reads from a repository's ``.open-swe`` directory."""

import hashlib
import json
import logging
import random
from collections import Counter
from datetime import timedelta
from fnmatch import fnmatchcase
from typing import Annotated

import httpx2
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    ValidatorFunctionWrapHandler,
    field_validator,
)

from openswe.github.http import (
    GitHubAppUnavailable,
    GitHubClient,
    GitHubError,
    RepoClient,
    RepoFileUnreadableError,
)
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequestPayload

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


class SupplementaryReviewArea(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    label: str = Field(min_length=1, max_length=300)
    github_login: str = Field(alias="githubLogin", pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")


class SupplementaryReviewPolicy(BaseModel):
    """A specialist review a repository asks for alongside engineering review, such as design."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    label: str = Field(min_length=1, max_length=60)
    team: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")
    paths: list[str] = Field(min_length=1)
    exclude: list[str] = Field(default_factory=list)
    criteria: str = Field(min_length=1, max_length=2000)
    areas: dict[
        Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")], SupplementaryReviewArea
    ] = Field(default_factory=dict)

    @field_validator("areas", mode="wrap")
    @classmethod
    def _valid_areas(
        cls, raw: object, handler: ValidatorFunctionWrapHandler
    ) -> dict[str, SupplementaryReviewArea]:
        try:
            areas: dict[str, SupplementaryReviewArea] = handler(raw)
            if "unmapped" in areas:
                raise ValueError("'unmapped' is reserved for team rotation")
        except ValueError:
            logger.warning(
                "Ignoring invalid supplementary review areas; falling back to team rotation",
                exc_info=True,
            )
            return {}
        return areas

    def matches(self, path: str) -> bool:
        return any(fnmatchcase(path, pattern) for pattern in self.paths) and not any(
            fnmatchcase(path, pattern) for pattern in self.exclude
        )


class ReviewFile(BaseModel):
    filename: str


class RepoSettings(BaseModel):
    """``.open-swe/settings.json``; unknown keys are ignored so the file can grow."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    supplementary_reviews: list[SupplementaryReviewPolicy] = Field(
        default_factory=list, alias="supplementaryReviews"
    )
    review_allowed_authors: list[str] = Field(default_factory=list, alias="reviewAllowedAuthors")
    review_channel: str = Field("", alias="reviewChannel")
    review_channel_rules: list[ReviewChannelRule] = Field(
        default_factory=list, alias="reviewChannelRules"
    )

    @field_validator("supplementary_reviews", mode="before")
    @classmethod
    def _valid_policies(cls, raw: object) -> list[SupplementaryReviewPolicy]:
        """Drop invalid or duplicate policies so one mistake never discards the rest of the file."""
        if not isinstance(raw, list):
            logger.warning("supplementaryReviews is not a list; ignoring it")
            return []
        policies: list[SupplementaryReviewPolicy] = []
        for index, entry in enumerate(raw):
            try:
                policy = SupplementaryReviewPolicy.model_validate(entry)
            except ValidationError:
                logger.warning(
                    "Ignoring an invalid supplementary review policy",
                    extra={"policy_index": index},
                    exc_info=True,
                )
                continue
            if any(existing.id == policy.id for existing in policies):
                logger.warning(
                    "Ignoring a duplicate supplementary review policy",
                    extra={"policy_id": policy.id},
                )
                continue
            policies.append(policy)
        return policies

    @classmethod
    def problems(cls, content: str) -> list[str]:
        """Why ``content`` is not a valid settings file, including entries reading would drop."""
        try:
            raw = json.loads(content)
        except ValueError as exc:
            return [f"`{SETTINGS_PATH}` is not valid JSON: {exc}"]
        try:
            cls.model_validate(raw)
        except ValidationError as exc:
            return [f"`{SETTINGS_PATH}`: {cls.describe(exc)}"]
        entries = raw.get("supplementaryReviews", []) if isinstance(raw, dict) else []
        if not isinstance(entries, list):
            return [f"`{SETTINGS_PATH}`: supplementaryReviews must be a list"]
        problems: list[str] = []
        seen: set[str] = set()
        for index, entry in enumerate(entries):
            try:
                policy = SupplementaryReviewPolicy.model_validate(entry)
            except ValidationError as exc:
                problems.append(
                    f"`{SETTINGS_PATH}`: supplementaryReviews[{index}]: {cls.describe(exc)}"
                )
                continue
            if policy.id in seen:
                problems.append(
                    f"`{SETTINGS_PATH}`: supplementaryReviews[{index}]: duplicate id {policy.id!r}"
                )
            seen.add(policy.id)
        return problems

    @staticmethod
    def describe(exc: ValidationError) -> str:
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'value'}: {error['msg']}"
            for error in exc.errors()
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

    def _channel_cache_key(self, pull: PullRequestClient) -> str:
        routing = self.model_dump_json(include={"review_channel", "review_channel_rules"})
        version = hashlib.sha256(routing.encode()).hexdigest()
        return f"review-channel:v1:{pull.repo.full_name.lower()}:{pull.number}:{version}"

    async def _cached_channel(self, key: str) -> str | None:
        from langgraph_api.cache import cache_get

        try:
            channel: object = await cache_get(key)
        except Exception:
            logger.warning("Review channel cache read failed", exc_info=True)
            return None
        return (
            channel if isinstance(channel, str) and channel in self.review_channels | {""} else None
        )

    async def _remember_channel(self, key: str, revision: str, channel: str) -> None:
        from langgraph_api.cache import cache_set

        try:
            await cache_set(f"{key}:{revision}", channel, SETTINGS_MAX_AGE)
            await cache_set(key, channel, SETTINGS_MAX_AGE)
        except Exception:
            logger.warning("Review channel cache write failed", exc_info=True)

    async def channel_for_pr(
        self,
        pull: PullRequestClient,
        *,
        snapshot: PullRequestPayload | None = None,
        for_display: bool = False,
    ) -> str:
        """Route the current diff; only display reads may fall back when its revision is unavailable.

        ``snapshot`` is the pull request as the caller already read it, so it isn't read again.
        """
        if not self.review_channel_rules:
            return self.review_channel.strip()
        key = self._channel_cache_key(pull)
        if snapshot is None:
            try:
                payload = await pull.pull()
            except httpx2.HTTPError as exc:
                transient = isinstance(exc, httpx2.TransportError) or (
                    isinstance(exc, httpx2.HTTPStatusError)
                    and (
                        500 <= exc.response.status_code < 600
                        or GitHubError(exc.response).rate_limited
                    )
                )
                if (
                    for_display
                    and transient
                    and (cached := await self._cached_channel(key)) is not None
                ):
                    logger.warning(
                        "Displaying the last review channel while GitHub is unavailable",
                        exc_info=True,
                    )
                    return cached
                raise
            snapshot = PullRequestPayload.model_validate(payload)
        if not snapshot.head_sha or not snapshot.base_sha:
            raise ValueError("Cannot route a pull request without its head and base commits")
        revision = f"{snapshot.head_sha}:{snapshot.base_sha}"
        cached = await self._cached_channel(f"{key}:{revision}")
        if cached is not None:
            await self._remember_channel(key, revision, cached)
            return cached
        files = TypeAdapter(list[ReviewFile]).validate_python(
            await pull.files(max_pages=_ROUTED_FILE_PAGES)
        )
        if len(files) >= _ROUTED_FILE_PAGES * 100 or (
            snapshot.changed_files is not None and len(files) != snapshot.changed_files
        ):
            raise ValueError("Cannot route a pull request with an incomplete changed-file list")
        current = PullRequestPayload.model_validate(await pull.pull())
        if (current.head_sha, current.base_sha) != (snapshot.head_sha, snapshot.base_sha):
            raise ValueError("Pull request changed while reading its review channel")
        channel = self.channel_for_files([file.filename for file in files])
        await self._remember_channel(key, revision, channel)
        return channel

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

        Unreadable settings read as empty without a usable cache; failed refreshes retain good values.
        """
        from langgraph_api.cache import swr

        async def load() -> RepoSettings:
            try:
                async with GitHubClient.as_app(owner, repo) as github:
                    return await cls.fetch(github.repo(owner, repo), strict=True)
            except GitHubAppUnavailable:
                logger.warning(
                    "No GitHub App token to read repository settings",
                    extra={"repository": f"{owner}/{repo}"},
                )
                return cls()

        key = f"repo-settings:{owner}/{repo}".lower()
        try:
            result = await swr(
                key, load, fresh_for=SETTINGS_FRESH_FOR, max_age=SETTINGS_MAX_AGE, model=cls
            )
        except RepoFileUnreadableError:
            logger.warning(
                "Repository settings unavailable without a usable cache; reading as empty",
                extra={"repository": f"{owner}/{repo}"},
                exc_info=True,
            )
            return cls()
        return result.value
