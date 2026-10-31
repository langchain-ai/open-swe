"""Deterministic reviewer picks: code owners and recent authors of the changed files.

Top-ranked contributors have more than three commits or more than 20% of repository commits
in the last 30 days, including a change of at least 20 relevant lines. If none
qualifies, smaller recent contributors are considered instead. They come
from one CODEOWNERS area at a time, the one owning the most changed files first.
Each scores the changed files they own plus those they changed within
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

from openswe.dashboard.profiles import PROFILES
from openswe.expedited_review.eligibility import ChangedFile
from openswe.github.codeowners import CodeOwners
from openswe.github.http import GitHubAppUnavailable, GitHubClient, RepoClient
from openswe.github.org_membership import team_members
from openswe.github.pull_request_status import PullRequestClient
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.availability import SlackUserInfo
from openswe.slack.client import get_slack_user_info
from openswe.users import User

logger = logging.getLogger(__name__)

HISTORY_WINDOW = timedelta(days=30)
_COMMIT_THRESHOLD = 3
_REPO_SHARE_THRESHOLD = 0.2
_MIN_CHANGED_LINES = 20
HISTORY_MAX_FILES = 30
_HISTORY_MAX_COMMITS = 100
_REPO_HISTORY_MAX_PAGES = 10
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

    sha: str
    author: _Login | None = None


_COMMITS = TypeAdapter(list[_Commit])


def _is_bot(login: str) -> bool:
    return login.lower().endswith("[bot]")


@dataclass(frozen=True, slots=True)
class ReviewerAvailability:
    """Slack out-of-office status and Mon–Fri working hours in the person's timezone."""

    zone: ZoneInfo | None
    out_of_office: bool | None = False

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

    @classmethod
    async def for_user(cls, user: User) -> Self:
        if not user.slack_user_id:
            return cls(None)
        info = await get_slack_user_info(user.slack_user_id)
        if info is None:
            return cls(None, None)
        profile = SlackUserInfo.model_validate(info)
        away = await profile.out_of_office(datetime.now(UTC))
        name = profile.tz
        if not name:
            return cls(None, away)
        try:
            return cls(ZoneInfo(name), away)
        except ZoneInfoNotFoundError, ValueError:
            logger.warning(
                "Unknown Slack timezone; treating the person as always on shift",
                extra={"slack_user_id": user.slack_user_id, "slack_timezone": name},
            )
            return cls(None, away)


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
    eligible: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class NoCandidate:
    """A complete candidate search found nobody eligible."""


@dataclass(frozen=True, slots=True)
class Covered:
    """Every code-owner area already has a reviewer."""


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

    The first reviewer comes from the largest area; the rest get one reviewer each after an approval.
    """

    areas: tuple[Area, ...]

    def uncovered(self, logins: Collection[str]) -> list[Area]:
        """Areas none of ``logins`` owns."""
        lowered = {login.lower() for login in logins}
        return [area for area in self.areas if not area.owners & lowered]

    def of(self, login: str) -> list[Area]:
        return [area for area in self.areas if login.lower() in area.owners]

    def owned(self) -> Counter[str]:
        """How many changed files each owner owns."""
        counts: Counter[str] = Counter()
        for area in self.areas:
            for login in area.owners:
                counts[login] += len(area.files)
        return counts

    @classmethod
    async def build(cls, codeowners: CodeOwners, paths: list[str]) -> Self:
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
        return cls(tuple(sorted(areas, key=lambda area: (-len(area.files), area.handles))))

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
        except GitHubAppUnavailable:
            logger.warning(
                "No GitHub App token to read code-owner coverage",
                extra={"request_id": str(request.id)},
            )
            return None
        if codeowners is None:
            return None
        return await cls.build(codeowners, [changed.filename for changed in files])


@dataclass(frozen=True, slots=True)
class Contributors:
    regular: Counter[str]
    small: Counter[str]


async def recent_contributors(
    repo: RepoClient, ref: str | None, files: list[ChangedFile]
) -> Contributors:
    """Build both contributor tiers once with bounded history and commit-detail reads."""
    since = (datetime.now(UTC) - HISTORY_WINDOW).isoformat()
    busiest = sorted(files, key=lambda f: (-f.changed_lines, f.filename))[:HISTORY_MAX_FILES]
    paths = {changed.filename for changed in files}
    gate = asyncio.Semaphore(_HISTORY_CONCURRENCY)

    async def history(path: str) -> list[_Commit]:
        async with gate:
            return _COMMITS.validate_python(
                await repo.commits(path=path, since=since, ref=ref, max_pages=1)
            )

    repo_commits = _COMMITS.validate_python(
        await repo.commits(since=since, ref=ref, max_pages=_REPO_HISTORY_MAX_PAGES)
    )
    repo_commits = list({commit.sha: commit for commit in repo_commits}.values())
    counts = Counter(
        commit.author.login.lower()
        for commit in repo_commits
        if commit.author is not None and commit.author.type == "User"
    )
    complete = len(repo_commits) < _REPO_HISTORY_MAX_PAGES * 100
    regular = {
        login
        for login, count in counts.items()
        if count > _COMMIT_THRESHOLD
        or (complete and count > len(repo_commits) * _REPO_SHARE_THRESHOLD)
    }
    histories = await asyncio.gather(*(history(f.filename) for f in busiest))
    small: dict[str, set[str]] = {}
    commits: dict[str, _Commit] = {}
    for file, history_items in zip(busiest, histories, strict=True):
        for commit in history_items:
            if commit.author is not None and commit.author.type == "User":
                small.setdefault(commit.author.login.lower(), set()).add(file.filename)
                commits.setdefault(commit.sha, commit)

    async def contribution(commit: _Commit) -> tuple[str, set[str]] | None:
        if commit.author is None or commit.author.login.lower() not in regular:
            return None
        async with gate:
            changed = TypeAdapter(list[ChangedFile]).validate_python(
                (await repo.commit(commit.sha)).get("files", [])
            )
        relevant = [file for file in changed if file.filename in paths]
        if sum(file.changed_lines for file in relevant) < _MIN_CHANGED_LINES:
            return None
        return commit.author.login.lower(), {file.filename for file in relevant}

    touched: dict[str, set[str]] = {}
    inspected = [
        commit
        for commit in commits.values()
        if commit.author and commit.author.login.lower() in regular
    ][:_HISTORY_MAX_COMMITS]
    for item in await asyncio.gather(*(contribution(commit) for commit in inspected)):
        if item is not None:
            login, changed_paths = item
            touched.setdefault(login, set()).update(changed_paths)
    return Contributors(
        Counter({login: len(changed_paths) for login, changed_paths in touched.items()}),
        Counter({login: len(changed_paths) for login, changed_paths in small.items()}),
    )


async def choose_reviewer(
    request: HumanReviewRequest, *, area: Area | None = None
) -> Pick | Wait | NoCandidate | Covered | None:
    """Pick or wait, distinguish exhausted and covered searches, and return None on read failure."""
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
    except GitHubAppUnavailable, httpx2.HTTPError, ValueError, ValidationError:
        logger.warning("Could not complete reviewer search", extra=extra, exc_info=True)
        return None


async def _choose_reviewer(
    request: HumanReviewRequest,
    pull: PullRequestClient,
    area: Area | None,
    extra: dict[str, object],
) -> Pick | Wait | NoCandidate | Covered | None:
    pr = request.pull_request
    files = await ChangedFile.of_pull(pull)
    if not files:
        logger.warning("Could not read the changed files to pick a reviewer", extra=extra)
        return None
    ref = pr.base_ref or None
    codeowners, contributors = await asyncio.gather(
        CodeOwners.fetch(pull.repo, ref),
        recent_contributors(pull.repo, ref, files),
    )
    coverage = (
        await Coverage.build(codeowners, [changed.filename for changed in files])
        if codeowners is not None
        else Coverage(())
    )
    touched = contributors.small
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
    logins = sorted(touched.keys() - {author})
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
        elif not await PROFILES.allows_auto_human_review(user.github_login or login):
            skipped[login] = "opted_out"
        elif request.is_author(user.id, login):
            skipped[login] = "author"
        elif user.id in taken:
            skipped[login] = "already_on_request"
        elif user.id in missed:
            skipped[login] = "missed_pick"
        else:
            people[login] = user
    hours = dict(
        zip(
            people,
            await asyncio.gather(
                *(ReviewerAvailability.for_user(user) for user in people.values())
            ),
            strict=True,
        )
    )
    for login in list(people):
        if hours[login].out_of_office is True:
            skipped[login] = "out_of_office"
            del people[login]
        elif hours[login].out_of_office is None:
            logger.warning(
                "Reviewer availability is unknown; retaining candidate for normal eligibility checks",
                extra={**extra, "github_login": login},
            )
    logger.info(
        "Filtered reviewer candidates",
        extra={**extra, "eligible": sorted(people), "skipped": skipped},
    )
    fallback = False
    if area is None and coverage.areas:
        on_request = [p.github_login for p in request.participants if p.decision != "expired"]
        if not (open_areas := coverage.uncovered(on_request)):
            logger.info("Every code owner area already has a reviewer", extra=extra)
            return Covered()
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
        return NoCandidate()
    writable = await asyncio.gather(*(pull.repo.can_write(login) for login in people))
    people = {
        login: user
        for (login, user), allowed in zip(people.items(), writable, strict=True)
        if allowed
    }
    regular_people = {
        login: user for login, user in people.items() if login in contributors.regular
    }
    if regular_people:
        people = regular_people
        touched = contributors.regular
    if not people:
        return NoCandidate()
    loads = await HumanReviewRequest.open_review_counts(
        [user.id for user in people.values()], excluding=request.id
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
        return Pick(
            people[candidate.login].login_for("github") or candidate.login,
            reason,
            frozenset(people),
        )
    logger.info("No candidate reviewer has write access", extra=extra)
    return NoCandidate()
