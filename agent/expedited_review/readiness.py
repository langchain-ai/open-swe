"""When a pull request revision is ready to be voted on.

Ready means: open, not a draft, no merge conflict, no check still running, no
required check failing, no unresolved review thread, no standing request for
changes, and — where Open SWE reviews the repository — an Open SWE review
published for this exact head SHA. Silence from a reviewer is not completion.

A failing check that GitHub does not require does not block the vote; it is
named on the card so the voters approve with their eyes open.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx2

from agent.baby_sit import aggregate_check_state
from agent.github.ci import fetch_pr, list_check_runs, list_commit_statuses
from agent.github.http import GITHUB_API_BASE, github_client, github_request
from agent.github.pull_request_status import fetch_unresolved_review_threads
from agent.github.pull_requests import PullRequest
from agent.review.enabled_repos import is_review_repo_enabled

logger = logging.getLogger(__name__)


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
    failures_are_required: bool = True
    changes_requested_by: list[str] = field(default_factory=list)
    open_swe_review_required: bool = False
    open_swe_reviewed_head: bool = False
    allowed_merge_methods: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Readiness:
    snapshot: PullRequestSnapshot
    blockers: list[str]

    @property
    def ready(self) -> bool:
        return not self.blockers

    @property
    def terminal(self) -> bool:
        """The PR can never become ready in its current form."""
        return self.snapshot.state != "open" or self.snapshot.mergeable is False


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
    if snapshot.unresolved_threads:
        noun = "thread" if snapshot.unresolved_threads == 1 else "threads"
        blockers.append(f"{snapshot.unresolved_threads} unresolved review {noun}")
    if snapshot.changes_requested_by:
        blockers.append(f"changes requested by {', '.join(snapshot.changes_requested_by)}")
    if snapshot.open_swe_review_required and not snapshot.open_swe_reviewed_head:
        blockers.append("Open SWE has not finished reviewing this commit")
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


async def _fetch_reviews(
    client: httpx2.AsyncClient, owner: str, repo: str, number: int
) -> list[dict[str, Any]] | None:
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}/reviews"
    collected: list[dict[str, Any]] = []
    page = 1
    try:
        while True:
            response = await github_request(
                client, "GET", url, params={"per_page": "100", "page": str(page)}
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                return None
            collected.extend(item for item in data if isinstance(item, dict))
            if len(data) < 100:
                return collected
            page += 1
    except httpx2.HTTPError, ValueError:
        return None


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


async def assess_readiness(
    *, owner: str, repo: str, pr_number: int, token: str
) -> Readiness | None:
    """Fetch the PR and everything that gates a vote; ``None`` when GitHub was unavailable."""
    pr = await fetch_pr(owner=owner, repo=repo, pr_number=pr_number, token=token)
    if pr is None:
        return None
    head = pr.get("head")
    head_sha = head.get("sha") if isinstance(head, Mapping) else None
    if not isinstance(head_sha, str) or not head_sha:
        return None
    user = pr.get("user")
    author = user.get("login") if isinstance(user, Mapping) else None
    mergeable = pr.get("mergeable")

    check_runs = await list_check_runs(owner=owner, repo=repo, ref=head_sha, token=token)
    statuses = await list_commit_statuses(owner=owner, repo=repo, ref=head_sha, token=token)
    if check_runs is None or statuses is None:
        return None
    async with github_client(token=token) as client:
        threads = await fetch_unresolved_review_threads(client, owner, repo, pr_number)
        reviews = await _fetch_reviews(client, owner, repo, pr_number)
    if threads is None or reviews is None:
        return None

    review_required = await is_review_repo_enabled(owner, repo)
    reviewed_head = False
    if review_required:
        try:
            stored = await PullRequest.get(owner, repo, pr_number)
        except Exception:
            logger.warning(
                "Pull request registry unavailable while checking Open SWE review",
                extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": pr_number},
                exc_info=True,
            )
            return None
        reviewed_head = stored is not None and any(
            review.head_sha == head_sha for review in stored.reviews
        )

    check_state, failures = aggregate_check_state(check_runs, statuses)
    author_login = author if isinstance(author, str) else ""
    mergeable_state = str(pr.get("mergeable_state") or "")
    snapshot = PullRequestSnapshot(
        state=str(pr.get("state") or ""),
        merged=bool(pr.get("merged")) or isinstance(pr.get("merged_at"), str),
        draft=bool(pr.get("draft")),
        head_sha=head_sha,
        title=str(pr.get("title") or ""),
        author=author_login,
        mergeable=mergeable if isinstance(mergeable, bool) else None,
        mergeable_state=mergeable_state,
        check_state=check_state,
        unresolved_threads=len(threads),
        failing_checks=sorted({str(failure["name"]) for failure in failures}),
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
        open_swe_review_required=review_required,
        open_swe_reviewed_head=reviewed_head,
        allowed_merge_methods=_merge_methods(pr),
    )
    return Readiness(snapshot=snapshot, blockers=readiness_blockers(snapshot))
