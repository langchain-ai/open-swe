"""When a pull request revision is ready to merge on its expedited approvals.

Ready means: open, not a draft, no merge conflict, no check still running, no
required check failing or yet to report, no unresolved review thread, and no
standing request for changes.

A failing check that GitHub does not require does not block the merge.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel

from openswe.baby_sit import aggregate_check_state
from openswe.github.ci import CommitChecks, RequiredCheck
from openswe.github.http import or_none
from openswe.github.pull_request_status import Mergeability, PullRequestClient
from openswe.github.pull_requests import PullRequestPayload


@dataclass(slots=True)
class PullRequestSnapshot:
    """Everything readiness looks at, fetched once."""

    state: str
    merged: bool
    draft: bool
    head_sha: str
    title: str
    author: str
    mergeable: bool | None
    mergeable_state: str
    check_state: str
    unresolved_threads: int
    failing_checks: list[str] = field(default_factory=list)
    unreported_required_checks: list[str] = field(default_factory=list)
    failures_are_required: bool = True
    changes_requested_by: list[str] = field(default_factory=list)
    allowed_merge_methods: list[str] = field(default_factory=list)
    approved_review_ids: frozenset[int] = frozenset()
    # The latest time any check or status on the head finished.
    checks_finished_at: datetime | None = None

    @property
    def green(self) -> bool:
        """Open, not a draft, conflict-free, and every check passed with none still to report."""
        return (
            self.state == "open"
            and not self.merged
            and not self.draft
            and self.mergeable is not False
            and self.mergeable_state != "dirty"
            and self.check_state == "success"
            and not self.unreported_required_checks
        )


class _ReviewState(BaseModel):
    id: int | None = None
    state: str = ""


class _CheckTimes(BaseModel):
    completed_at: datetime | None = None
    updated_at: datetime | None = None


def _checks_finished_at(
    check_runs: list[dict[str, Any]], statuses: list[dict[str, Any]]
) -> datetime | None:
    stamps = [_CheckTimes.model_validate(run).completed_at for run in check_runs]
    stamps += [_CheckTimes.model_validate(status).updated_at for status in statuses]
    return max((stamp for stamp in stamps if stamp is not None), default=None)


@dataclass(frozen=True, slots=True)
class Readiness:
    snapshot: PullRequestSnapshot
    blockers: list[str]

    @property
    def ready(self) -> bool:
        return not self.blockers

    @classmethod
    async def assess(cls, pull: PullRequestClient) -> Self | None:
        """Fetch the PR and everything that gates a vote; ``None`` when GitHub was unavailable."""
        pr = await or_none(pull.pull())
        if pr is None:
            return None
        head = pr.get("head")
        head_sha = head.get("sha") if isinstance(head, Mapping) else None
        if not isinstance(head_sha, str) or not head_sha:
            return None
        user = pr.get("user")
        author = user.get("login") if isinstance(user, Mapping) else None

        checks = await CommitChecks.read(pull.repo, head_sha)
        if checks is None:
            return None
        required = await RequiredCheck.for_branch(
            pull.repo, PullRequestPayload.model_validate(pr).base_ref
        )
        if required is None:
            return None
        threads = await pull.unresolved_threads()
        reviews = await or_none(pull.reviews())
        mergeability = await pull.mergeability()
        if threads is None or reviews is None:
            return None
        mergeable, mergeable_state = _resolve_mergeability(pr, mergeability)

        check_state, failures = aggregate_check_state(checks.runs, checks.statuses)
        author_login = author if isinstance(author, str) else ""
        snapshot = PullRequestSnapshot(
            state=str(pr.get("state") or ""),
            merged=bool(pr.get("merged")) or isinstance(pr.get("merged_at"), str),
            draft=bool(pr.get("draft")),
            head_sha=head_sha,
            title=str(pr.get("title") or ""),
            author=author_login,
            mergeable=mergeable,
            mergeable_state=mergeable_state,
            check_state=check_state,
            unresolved_threads=len(threads),
            failing_checks=sorted({str(failure["name"]) for failure in failures}),
            unreported_required_checks=checks.unreported(required),
            # GitHub says "unstable" when the pull request is mergeable and only
            # checks it does not require are unhappy, and "blocked" when a required
            # one is. Trusting it keeps us from having to read branch protection,
            # which needs admin, and from guessing at ruleset precedence.
            failures_are_required=mergeable_state != "unstable",
            changes_requested_by=sorted(
                login
                for login, state in _latest_reviews_by_user(reviews, author_login).items()
                if state == "CHANGES_REQUESTED"
            ),
            allowed_merge_methods=_merge_methods(pr),
            approved_review_ids=frozenset(
                parsed.id
                for parsed in map(_ReviewState.model_validate, reviews)
                if parsed.state == "APPROVED" and parsed.id is not None
            ),
            checks_finished_at=_checks_finished_at(checks.runs, checks.statuses),
        )
        return cls(snapshot=snapshot, blockers=readiness_blockers(snapshot))


def _failed_check_blocker(snapshot: PullRequestSnapshot) -> str:
    if not snapshot.failing_checks:
        return "a check finished without success"
    noun = "check" if len(snapshot.failing_checks) == 1 else "checks"
    return f"failing {noun}: {', '.join(snapshot.failing_checks)}"


def readiness_blockers(snapshot: PullRequestSnapshot) -> list[str]:
    blockers: list[str] = []
    if snapshot.merged:
        blockers.append("the pull request is already merged")
    elif snapshot.state != "open":
        blockers.append("the pull request is closed")
    if snapshot.draft:
        blockers.append("the pull request is a draft")
    if snapshot.mergeable is False or snapshot.mergeable_state == "dirty":
        blockers.append("the pull request has merge conflicts")
    elif snapshot.mergeable is None:
        blockers.append("GitHub is still computing mergeability")
    if snapshot.check_state == "pending":
        blockers.append("checks are still running")
    elif snapshot.check_state in {"failure", "blocked"} and snapshot.failures_are_required:
        blockers.append(_failed_check_blocker(snapshot))
    if snapshot.unreported_required_checks:
        names = ", ".join(snapshot.unreported_required_checks)
        blockers.append(f"required checks have not reported yet: {names}")
    if snapshot.unresolved_threads:
        noun = "thread" if snapshot.unresolved_threads == 1 else "threads"
        blockers.append(f"{snapshot.unresolved_threads} unresolved review {noun}")
    if snapshot.changes_requested_by:
        blockers.append(f"changes requested by {', '.join(snapshot.changes_requested_by)}")
    return blockers


def _latest_reviews_by_user(reviews: list[dict[str, Any]], author: str) -> dict[str, str]:
    latest: dict[str, str] = {}
    for review in reviews:
        user = review.get("user")
        login = user.get("login") if isinstance(user, Mapping) else None
        state = review.get("state")
        if not isinstance(login, str) or not isinstance(state, str):
            continue
        if login.lower() == author.lower() or state in {"COMMENTED", "PENDING"}:
            continue
        latest[login] = state
    return latest


async def review_authors(pull: PullRequestClient) -> set[str] | None:
    """Lowercased logins of everyone who submitted a review, comment-only ones included."""
    reviews = await or_none(pull.reviews())
    if reviews is None:
        return None
    authors: set[str] = set()
    for review in reviews:
        user = review.get("user")
        login = user.get("login") if isinstance(user, Mapping) else None
        if isinstance(login, str) and review.get("state") != "PENDING":
            authors.add(login.lower())
    return authors


async def latest_review_states(pull: PullRequestClient, author: str) -> dict[str, str] | None:
    """Each non-author reviewer's latest ``APPROVED``/``CHANGES_REQUESTED``/``DISMISSED`` state."""
    reviews = await or_none(pull.reviews())
    if reviews is None:
        return None
    return _latest_reviews_by_user(reviews, author)


async def review_standings(pull: PullRequestClient, author: str) -> dict[str, str] | None:
    """Like ``latest_review_states``, but ``COMMENTED`` for reviewers who only left comments."""
    reviews = await or_none(pull.reviews())
    if reviews is None:
        return None
    standings = _latest_reviews_by_user(reviews, author)
    for review in reviews:
        user = review.get("user")
        login = user.get("login") if isinstance(user, Mapping) else None
        if (
            isinstance(login, str)
            and review.get("state") == "COMMENTED"
            and login.lower() != author.lower()
            and standings.get(login, "DISMISSED") == "DISMISSED"
        ):
            standings[login] = "COMMENTED"
    return standings


def _resolve_mergeability(
    pr: Mapping[str, Any], live: Mergeability | None
) -> tuple[bool | None, str]:
    """GraphQL's verdict where it has one, falling back to what REST returned."""
    rest = pr.get("mergeable")
    rest_mergeable = rest if isinstance(rest, bool) else None
    rest_state = str(pr.get("mergeable_state") or "")
    if live is None or live.mergeable is None:
        return rest_mergeable, rest_state
    return live.mergeable, live.merge_state or rest_state


def _merge_methods(pr: Mapping[str, Any]) -> list[str]:
    base = pr.get("base")
    base_repo = base.get("repo") if isinstance(base, Mapping) else None
    if not isinstance(base_repo, Mapping):
        return []
    methods = [
        method
        for method, flag in (
            ("squash", "allow_squash_merge"),
            ("merge", "allow_merge_commit"),
            ("rebase", "allow_rebase_merge"),
        )
        if base_repo.get(flag) is not False
    ]
    return methods
