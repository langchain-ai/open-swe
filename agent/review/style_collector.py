"""Collect historical PR review samples from GitHub for style analysis."""

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..utils.github_http import github_client, github_paginate, github_request, github_url

logger = logging.getLogger(__name__)

DEFAULT_MAX_PRS = 20
DEFAULT_MAX_REVIEWERS = 10
DEFAULT_MAX_SAMPLES_PER_REVIEWER = 6
MIN_COMMENT_CHARS = 20
COLLECT_TIMEOUT = httpx.Timeout(90.0, connect=10.0)
_BOT_SUFFIX = "[bot]"


@dataclass
class ReviewSample:
    pr_number: int
    reviewer_login: str
    kind: str
    body: str
    state: str = ""
    path: str | None = None
    submitted_at: str | None = None


@dataclass
class ReviewStyleSamples:
    full_name: str
    owner: str
    name: str
    top_reviewers: list[str] = field(default_factory=list)
    samples: list[ReviewSample] = field(default_factory=list)
    prs_scanned: int = 0
    reviews_scanned: int = 0


def _is_bot_login(login: str | None) -> bool:
    if not login:
        return True
    return login.endswith(_BOT_SUFFIX) or login.endswith("-bot")


def _is_bot_user(user: dict[str, Any] | None) -> bool:
    if not isinstance(user, dict):
        return True
    if user.get("type") == "Bot":
        return True
    login = user.get("login")
    return _is_bot_login(login if isinstance(login, str) else None)


def _substantive_body(body: str | None) -> str | None:
    text = (body or "").strip()
    if len(text) < MIN_COMMENT_CHARS:
        return None
    return text[:4000]


async def _recent_merged_prs(
    client: httpx.AsyncClient,
    *,
    owner: str,
    repo: str,
    max_prs: int,
) -> list[dict[str, Any]]:
    """Return recently merged PRs via the issues search API (reliable on busy repos)."""
    r = await github_request(
        client,
        "GET",
        github_url("/search/issues"),
        params={
            "q": f"repo:{owner}/{repo} is:pr is:merged",
            "sort": "updated",
            "order": "desc",
            "per_page": min(max_prs, 100),
        },
    )
    r.raise_for_status()
    body = r.json()
    items = body.get("items", []) if isinstance(body, dict) else []
    merged: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        number = item.get("number")
        if not isinstance(number, int):
            continue
        merged.append({"number": number, "title": item.get("title", "")})
    if not merged:
        logger.warning(
            "search returned 0 merged PRs for %s/%s (status=%s total_count=%s)",
            owner,
            repo,
            r.status_code,
            body.get("total_count") if isinstance(body, dict) else "?",
        )
    return merged


async def collect_review_samples(
    token: str,
    owner: str,
    repo: str,
    *,
    max_prs: int = DEFAULT_MAX_PRS,
    max_reviewers: int = DEFAULT_MAX_REVIEWERS,
    max_samples_per_reviewer: int = DEFAULT_MAX_SAMPLES_PER_REVIEWER,
) -> ReviewStyleSamples:
    """Sample recent merged PR feedback to identify reviewer style."""
    full_name = f"{owner}/{repo}"

    raw_entries: list[tuple[str, int, ReviewSample]] = []
    reviewer_counts: Counter[str] = Counter()

    async with github_client(token=token, timeout=COLLECT_TIMEOUT) as client:
        merged_prs = await _recent_merged_prs(client, owner=owner, repo=repo, max_prs=max_prs)

        for pr in merged_prs:
            pr_number = pr.get("number")
            if not isinstance(pr_number, int):
                continue

            reviews_url = github_url(f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews")
            for review in await github_paginate(client, reviews_url, cap=100):
                if not isinstance(review, dict):
                    continue
                user = review.get("user")
                if _is_bot_user(user if isinstance(user, dict) else None):
                    continue
                login = (user or {}).get("login") if isinstance(user, dict) else None
                if not isinstance(login, str):
                    continue
                body = _substantive_body(review.get("body"))
                if not body:
                    continue
                reviewer_counts[login] += 1
                raw_entries.append(
                    (
                        login,
                        pr_number,
                        ReviewSample(
                            pr_number=pr_number,
                            reviewer_login=login,
                            kind="review",
                            state=str(review.get("state") or ""),
                            body=body,
                            submitted_at=review.get("submitted_at"),
                        ),
                    )
                )

            comments_url = github_url(f"/repos/{owner}/{repo}/pulls/{pr_number}/comments")
            for comment in await github_paginate(client, comments_url, cap=200):
                if not isinstance(comment, dict):
                    continue
                user = comment.get("user")
                if _is_bot_user(user if isinstance(user, dict) else None):
                    continue
                login = (user or {}).get("login") if isinstance(user, dict) else None
                if not isinstance(login, str):
                    continue
                body = _substantive_body(comment.get("body"))
                if not body:
                    continue
                path = comment.get("path")
                reviewer_counts[login] += 1
                raw_entries.append(
                    (
                        login,
                        pr_number,
                        ReviewSample(
                            pr_number=pr_number,
                            reviewer_login=login,
                            kind="inline",
                            body=body,
                            path=str(path) if isinstance(path, str) else None,
                            submitted_at=comment.get("created_at"),
                        ),
                    )
                )

            issue_comments_url = github_url(f"/repos/{owner}/{repo}/issues/{pr_number}/comments")
            for comment in await github_paginate(client, issue_comments_url, cap=100):
                if not isinstance(comment, dict):
                    continue
                user = comment.get("user")
                if _is_bot_user(user if isinstance(user, dict) else None):
                    continue
                login = (user or {}).get("login") if isinstance(user, dict) else None
                if not isinstance(login, str):
                    continue
                body = _substantive_body(comment.get("body"))
                if not body:
                    continue
                reviewer_counts[login] += 1
                raw_entries.append(
                    (
                        login,
                        pr_number,
                        ReviewSample(
                            pr_number=pr_number,
                            reviewer_login=login,
                            kind="issue",
                            body=body,
                            submitted_at=comment.get("created_at"),
                        ),
                    )
                )

        top_reviewers = [login for login, _ in reviewer_counts.most_common(max_reviewers)]
        top_set = set(top_reviewers)

        per_reviewer: Counter[str] = Counter()
        samples: list[ReviewSample] = []

        for login, _pr_number, sample in raw_entries:
            if login not in top_set:
                continue
            if per_reviewer[login] >= max_samples_per_reviewer:
                continue
            samples.append(sample)
            per_reviewer[login] += 1

    return ReviewStyleSamples(
        full_name=full_name,
        owner=owner,
        name=repo,
        top_reviewers=top_reviewers,
        samples=samples,
        prs_scanned=len(merged_prs),
        reviews_scanned=len(raw_entries),
    )


def format_samples_for_analyzer(samples: ReviewStyleSamples) -> str:
    """Render collected samples as context for the style-analyzer agent."""
    lines = [
        f"# Recent review samples for {samples.full_name}",
        "",
        f"Recently merged PRs scanned: {samples.prs_scanned}",
        f"Review summaries + inline comments collected: {samples.reviews_scanned}",
        f"Top reviewers ({len(samples.top_reviewers)}): {', '.join(samples.top_reviewers) or '(none)'}",
        "",
    ]
    if not samples.samples:
        lines.append(
            "Pre-collection found no substantive review text on recent merged PRs. "
            "You must browse merged PRs yourself with `gh` (reviews, "
            "pull comments, and issue comments) before saving."
        )
        return "\n".join(lines)

    by_reviewer: dict[str, list[ReviewSample]] = {}
    for s in samples.samples:
        by_reviewer.setdefault(s.reviewer_login, []).append(s)

    for login in samples.top_reviewers:
        reviewer_samples = by_reviewer.get(login, [])
        if not reviewer_samples:
            continue
        lines.append(f"## Reviewer: @{login}")
        for s in reviewer_samples:
            if s.kind == "inline":
                loc = f" ({s.path})" if s.path else ""
                lines.append(f"### PR #{s.pr_number} inline comment{loc}")
            elif s.kind == "issue":
                lines.append(f"### PR #{s.pr_number} issue comment")
            else:
                state = f", state={s.state}" if s.state else ""
                lines.append(f"### PR #{s.pr_number} review summary{state}")
            lines.append(s.body)
            lines.append("")
    return "\n".join(lines)
