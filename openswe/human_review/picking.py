"""Deterministic reviewer picks: code owners and recent authors of the changed files.

Candidates come from one CODEOWNERS area at a time, the one owning the most changed
files first. Each scores the changed files they own plus those they changed within
``HISTORY_WINDOW``, divided by one more than the open reviews they already have.
Only people inside their work hours are picked; when nobody is, the pick waits
for whoever's work day starts first. Anyone already on the request, or rotated
away from it for not accepting, is skipped.
"""

import asyncio
import logging
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx2
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from openswe.expedited_review.eligibility import ChangedFile
from openswe.github.codeowners import CodeOwners
from openswe.github.http import GitHubAppUnavailable, GitHubClient, RepoClient
from openswe.github.org_membership import team_members
from openswe.github.pull_request_status import PullRequestClient
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.client import get_slack_user_info
from openswe.users import User

logger = logging.getLogger(__name__)

HISTORY_WINDOW = timedelta(days=90)
HISTORY_MAX_FILES = 30
_HISTORY_CONCURRENCY = 8
WORK_START = time(9)
WORK_END = time(18)
_WORK_DAYS = frozenset(range(5))


class _Login(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str
    type: str = "User"


class _Commit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    author: _Login | None = None


_COMMITS = TypeAdapter(list[_Commit])


class _SlackProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tz: str = ""


def _is_bot(login: str) -> bool:
    return login.lower().endswith("[bot]")


@dataclass(frozen=True, slots=True)
class WorkHours:
    """Mon–Fri ``WORK_START``–``WORK_END`` in ``zone``; an unknown zone is always on shift."""

    zone: ZoneInfo | None

    @property
    def zone_name(self) -> str:
        return self.zone.key if self.zone is not None else ""

    def on_shift(self, now: datetime) -> bool:
        if self.zone is None:
            return True
        local = now.astimezone(self.zone)
        return local.weekday() in _WORK_DAYS and WORK_START <= local.time() < WORK_END

    def next_start(self, now: datetime) -> datetime:
        """``now`` while on shift, otherwise when the next work day starts."""
        if self.zone is None or self.on_shift(now):
            return now
        local = now.astimezone(self.zone)
        for offset in range(8):
            day = local.date() + timedelta(days=offset)
            start = datetime.combine(day, WORK_START, tzinfo=self.zone)
            if day.weekday() in _WORK_DAYS and start > local:
                return start.astimezone(UTC)
        raise AssertionError("a work day starts within a week")

    def after(self, start: datetime, duration: timedelta) -> datetime:
        """When ``duration`` of work time has passed since ``start``."""
        if self.zone is None:
            return start + duration
        cursor = self.next_start(start)
        remaining = duration
        while True:
            local = cursor.astimezone(self.zone)
            closing = datetime.combine(local.date(), WORK_END, tzinfo=self.zone)
            if remaining <= closing - local:
                return (local + remaining).astimezone(UTC)
            remaining -= closing - local
            cursor = self.next_start(closing)

    @classmethod
    async def for_user(cls, user: User) -> Self:
        if not user.slack_user_id:
            return cls(None)
        info = await get_slack_user_info(user.slack_user_id)
        name = _SlackProfile.model_validate(info).tz if info else ""
        if not name:
            return cls(None)
        try:
            return cls(ZoneInfo(name))
        except ZoneInfoNotFoundError, ValueError:
            logger.warning(
                "Unknown Slack timezone; treating the person as always on shift",
                extra={"slack_user_id": user.slack_user_id, "slack_timezone": name},
            )
            return cls(None)


@dataclass(frozen=True, slots=True)
class Candidate:
    login: str
    owned: int
    touched: int
    open_reviews: int = 0

    @property
    def score(self) -> float:
        return (self.owned + self.touched) / (1 + self.open_reviews)

    @classmethod
    def rank(cls, candidates: list[Self]) -> list[Self]:
        """Best first; ties go to fewer open reviews, then ownership, then history, then login."""
        return sorted(
            candidates,
            key=lambda c: (-c.score, c.open_reviews, -c.owned, -c.touched, c.login.lower()),
        )

    def reason(self, total_files: int) -> str:
        """Why them, addressed to them."""
        files = f"{total_files} changed file{'s' if total_files != 1 else ''}"
        parts: list[str] = []
        if self.owned:
            parts.append(f"own {self.owned} of the {files}")
        if self.touched:
            of = "of them" if self.owned else f"of the {files}"
            parts.append(f"changed {self.touched} {of} in the last {HISTORY_WINDOW.days} days")
        parts.append(
            f"have {self.open_reviews} other open review{'s' if self.open_reviews != 1 else ''}"
            if self.open_reviews
            else "have no other open reviews"
        )
        if len(parts) == 2:
            return f"You {parts[0]} and {parts[1]}."
        return f"You {', '.join(parts[:-1])}, and {parts[-1]}."


@dataclass(frozen=True, slots=True)
class Pick:
    login: str
    reason: str


@dataclass(frozen=True, slots=True)
class Wait:
    """Nobody eligible is on shift; ``login``'s work day starts first, at ``until``."""

    login: str
    until: datetime


@dataclass(frozen=True, slots=True)
class Area:
    """Changed files that share one set of CODEOWNERS owners."""

    handles: tuple[str, ...]
    owners: frozenset[str]
    files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Coverage:
    """A pull request's owned changed files split by owners, the largest area first.

    ``required`` is GitHub requiring a code owner's approval for every owned file; otherwise
    the areas only guide who reviews.
    """

    areas: tuple[Area, ...]
    required: bool = False

    def uncovered(self, logins: Collection[str]) -> list[Area]:
        """Areas none of ``logins`` owns."""
        lowered = {login.lower() for login in logins}
        return [area for area in self.areas if not area.owners & lowered]

    def missing(self, approvers: Collection[str]) -> list[Area]:
        """Areas GitHub still requires an owner's approval for before the pull request can merge."""
        return self.uncovered(approvers) if self.required else []

    def of(self, login: str) -> list[Area]:
        return [area for area in self.areas if login.lower() in area.owners]

    def overlap(self, login: str, other: str) -> bool:
        """Whether ``login`` and ``other`` own any of the same changed code."""
        return bool(set(self.of(login)) & set(self.of(other)))

    def satisfied(self, login: str, approvers: Collection[str]) -> bool:
        """Whether ``login`` owns some area and ``approvers`` cover every area they own."""
        left = self.uncovered(approvers)
        return bool(theirs := self.of(login)) and not any(area in left for area in theirs)

    def owned(self) -> Counter[str]:
        """How many changed files each owner owns."""
        counts: Counter[str] = Counter()
        for area in self.areas:
            for login in area.owners:
                counts[login] += len(area.files)
        return counts

    @classmethod
    async def build(
        cls, codeowners: CodeOwners, paths: list[str], *, required: bool = False
    ) -> Self:
        grouped: dict[tuple[str, ...], list[str]] = {}
        for path in paths:
            if handles := codeowners.owners_for(path):
                grouped.setdefault(handles, []).append(path)
        teams: dict[str, list[str]] = {}
        areas: list[Area] = []
        for handles, files in grouped.items():
            owners: set[str] = set()
            for owner in handles:
                handle = owner.removeprefix("@")
                if "/" not in handle:
                    owners.add(handle.lower())
                    continue
                if handle not in teams:
                    org, slug = handle.split("/", 1)
                    teams[handle] = await team_members(org, slug) or []
                    logger.info(
                        "Expanded a CODEOWNERS team",
                        extra={"github_team": handle, "members": len(teams[handle])},
                    )
                owners.update(login.lower() for login in teams[handle])
            areas.append(Area(handles, frozenset(owners), tuple(files)))
        return cls(
            tuple(sorted(areas, key=lambda area: (-len(area.files), area.handles))),
            required=required,
        )

    @classmethod
    async def load(cls, request: HumanReviewRequest) -> Self | None:
        """The request's coverage; ``None`` without CODEOWNERS or when GitHub cannot be read."""
        pr = request.pull_request
        try:
            async with PullRequestClient.as_app(pr.owner, pr.repo, pr.number) as pull:
                files = await ChangedFile.of_pull(pull)
                if not files:
                    return None
                codeowners = await CodeOwners.fetch(pull.repo, pr.base_ref or None)
                required = (
                    await pull.repo.requires_code_owner_review(pr.base_ref) if pr.base_ref else True
                )
        except GitHubAppUnavailable:
            logger.warning(
                "No GitHub App token to read code-owner coverage",
                extra={"request_id": str(request.id)},
            )
            return None
        if codeowners is None:
            return None
        return await cls.build(
            codeowners, [changed.filename for changed in files], required=required
        )


REVIEWER_INSTRUCTIONS_PATH = ".open-swe/REVIEWERS.md"
_REVIEWER_INSTRUCTIONS_MAX_CHARS = 10_000


@dataclass(frozen=True, slots=True)
class ReviewerInstructions:
    """A repository's ``.open-swe/REVIEWERS.md``: how its maintainers want reviewers picked.

    Read from the pull request's base branch, so a pull request cannot rewrite how its own
    reviewers are chosen.
    """

    text: str

    @classmethod
    async def load(cls, request: HumanReviewRequest) -> Self | None:
        """``None`` when the file is absent, empty, too large, or unreadable."""
        pr = request.pull_request
        try:
            async with GitHubClient.as_app(pr.owner, pr.repo) as github:
                text = await github.repo(pr.owner, pr.repo).read_file(
                    REVIEWER_INSTRUCTIONS_PATH,
                    pr.base_ref or None,
                    max_chars=_REVIEWER_INSTRUCTIONS_MAX_CHARS,
                )
        except GitHubAppUnavailable:
            logger.warning(
                "No GitHub App token to read reviewer instructions",
                extra={"request_id": str(request.id)},
            )
            return None
        return cls(text) if text else None


async def _touched(repo: RepoClient, ref: str | None, files: list[ChangedFile]) -> Counter[str]:
    since = (datetime.now(UTC) - HISTORY_WINDOW).isoformat()
    busiest = sorted(files, key=lambda f: (-f.changed_lines, f.filename))[:HISTORY_MAX_FILES]
    gate = asyncio.Semaphore(_HISTORY_CONCURRENCY)

    async def authors(path: str) -> set[str]:
        async with gate:
            try:
                commits = _COMMITS.validate_python(
                    await repo.commits(path=path, since=since, ref=ref)
                )
            except httpx2.HTTPError, ValueError, ValidationError:
                logger.warning(
                    "Could not read a changed file's history",
                    extra={"repository": repo.full_name, "path": path},
                    exc_info=True,
                )
                return set()
        return {
            commit.author.login.lower()
            for commit in commits
            if commit.author is not None and commit.author.type == "User"
        }

    touched: Counter[str] = Counter()
    for logins in await asyncio.gather(*(authors(f.filename) for f in busiest)):
        touched.update(logins)
    return touched


async def choose_reviewer(
    request: HumanReviewRequest, *, area: Area | None = None
) -> Pick | Wait | None:
    """The best on-shift owner of ``area``, when to try again, or ``None`` when nobody qualifies.

    Without ``area`` it is the first area nobody on the request owns, the largest first, falling
    back to anyone who changed the files; ``None`` once every area has someone.
    """
    pr = request.pull_request
    extra = {
        "request_id": str(request.id),
        "repository": f"{pr.owner}/{pr.repo}",
        "pr_number": pr.number,
    }
    logger.info("Picking a reviewer", extra={**extra, "base_ref": pr.base_ref})
    try:
        async with GitHubClient.as_app(pr.owner, pr.repo) as github:
            return await _choose_reviewer(
                request, github.repo(pr.owner, pr.repo).pull_request(pr.number), area, extra
            )
    except GitHubAppUnavailable:
        logger.warning("No GitHub App token to pick a reviewer", extra=extra)
        return None


async def _choose_reviewer(
    request: HumanReviewRequest,
    pull: PullRequestClient,
    area: Area | None,
    extra: dict[str, object],
) -> Pick | Wait | None:
    pr = request.pull_request
    files = await ChangedFile.of_pull(pull)
    if not files:
        logger.warning("Could not read the changed files to pick a reviewer", extra=extra)
        return None
    ref = pr.base_ref or None
    codeowners, touched = await asyncio.gather(
        CodeOwners.fetch(pull.repo, ref), _touched(pull.repo, ref, files)
    )
    coverage = (
        await Coverage.build(codeowners, [changed.filename for changed in files])
        if codeowners is not None
        else Coverage(())
    )
    owned = coverage.owned()
    logger.info(
        "Gathered reviewer candidates",
        extra={
            **extra,
            "changed_files": len(files),
            "history_files": min(len(files), HISTORY_MAX_FILES),
            "has_codeowners": codeowners is not None,
            "code_owners": dict(owned.most_common()),
            "recent_authors": dict(touched.most_common()),
        },
    )
    author = (pr.author or "").lower()
    logins = sorted((owned.keys() | touched.keys()) - {author})
    users = await asyncio.gather(*(User.for_login("github", login) for login in logins))
    taken = {p.user_id for p in request.participants if p.decision != "expired"}
    missed = {p.user_id for p in request.participants if p.decision == "expired"}
    people: dict[str, User] = {}
    skipped: dict[str, str] = {author: "author"} if author in owned or author in touched else {}
    for login, user in zip(logins, users, strict=True):
        if user is None:
            skipped[login] = "no_open_swe_account"
        elif _is_bot(login):
            skipped[login] = "bot"
        elif request.is_author(user.id, login):
            skipped[login] = "author"
        elif user.id in taken:
            skipped[login] = "already_on_request"
        elif user.id in missed:
            skipped[login] = "missed_pick"
        else:
            people[login] = user
    logger.info(
        "Filtered reviewer candidates",
        extra={**extra, "eligible": sorted(people), "skipped": skipped},
    )
    fallback = False
    if area is None and coverage.areas:
        on_request = [p.github_login for p in request.participants if p.decision != "expired"]
        if not (open_areas := coverage.uncovered(on_request)):
            logger.info("Every code owner area already has a reviewer", extra=extra)
            return None
        area, fallback = open_areas[0], True
    if area is not None:
        owners = {login: user for login, user in people.items() if login in area.owners}
        logger.info(
            "Narrowed reviewer candidates to a code owner area",
            extra={**extra, "code_owners": list(area.handles), "eligible": sorted(owners)},
        )
        if owners or not fallback:
            people = owners
    if not people:
        logger.info("No Open SWE user owns or recently changed these files", extra=extra)
        return None
    loads = await HumanReviewRequest.open_review_counts(
        [user.id for user in people.values()], excluding=request.id
    )
    hours = dict(
        zip(
            people,
            await asyncio.gather(*(WorkHours.for_user(user) for user in people.values())),
            strict=True,
        )
    )
    ranked = Candidate.rank(
        [
            Candidate(login, owned[login], touched[login], loads[user.id])
            for login, user in people.items()
        ]
    )
    now = datetime.now(UTC)
    logger.info(
        "Ranked reviewer candidates",
        extra={
            **extra,
            "ranking": [
                {
                    "github_login": c.login,
                    "owned": c.owned,
                    "touched": c.touched,
                    "open_reviews": c.open_reviews,
                    "score": round(c.score, 3),
                    "timezone": hours[c.login].zone_name,
                    "on_shift": hours[c.login].on_shift(now),
                    "next_start": hours[c.login].next_start(now).isoformat(),
                }
                for c in ranked
            ],
        },
    )
    by_start = sorted(ranked, key=lambda c: hours[c.login].next_start(now))
    for candidate in by_start:
        if not await pull.repo.can_write(candidate.login):
            logger.info(
                "Skipped a reviewer candidate without write access",
                extra={**extra, "github_login": candidate.login},
            )
            continue
        start = hours[candidate.login].next_start(now)
        if start > now:
            logger.info(
                "No eligible reviewer is on shift; waiting for the first work day to start",
                extra={**extra, "github_login": candidate.login, "until": start.isoformat()},
            )
            return Wait(candidate.login, start)
        reason = candidate.reason(len(files))
        logger.info(
            "Picked a reviewer",
            extra={
                **extra,
                "github_login": candidate.login,
                "score": round(candidate.score, 3),
                "owned": candidate.owned,
                "touched": candidate.touched,
                "open_reviews": candidate.open_reviews,
            },
        )
        return Pick(people[candidate.login].login_for("github") or candidate.login, reason)
    logger.info("No candidate reviewer has write access", extra=extra)
    return None
