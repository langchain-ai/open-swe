"""Collect historical PR review samples from GitHub for style analysis."""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MAX_PRS = 20
DEFAULT_MAX_REVIEWERS = 10
DEFAULT_MAX_SAMPLES_PER_REVIEWER = 6
MIN_COMMENT_CHARS = 20
GITHUB_API = "https://api.github.com"
_BOT_SUFFIX = "[bot]"


def generate_review_style_thread_id(owner: str, repo: str) -> str:
    stable_key = f"{owner}/{repo}/review-style"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, stable_key))


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


def github_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _paginate(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    cap: int = 500,
) -> list[Any]:
    out: list[Any] = []
    next_url: str | None = url
    first = True
    while next_url and len(out) < cap:
        params = {"per_page": "100"} if first else None
        r = await client.get(next_url, headers=headers, params=params)
        r.raise_for_status()
        page = r.json()
        if isinstance(page, list):
            out.extend(page)
        next_url = None
        link = r.headers.get("Link", "")
        for part in link.split(","):
            segments = [s.strip() for s in part.split(";")]
            if len(segments) >= 2 and 'rel="next"' in segments[1] and segments[0].startswith("<"):
                next_url = segments[0][1:-1]
                break
        first = False
    return out


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
    headers: dict[str, str],
    max_prs: int,
) -> list[dict[str, Any]]:
    """Return recently merged PRs via the issues search API (reliable on busy repos)."""
    r = await client.get(
        f"{GITHUB_API}/search/issues",
        headers=headers,
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
    headers = github_headers(token)

    raw_entries: list[tuple[str, int, ReviewSample]] = []
    reviewer_counts: Counter[str] = Counter()

    async with httpx.AsyncClient(timeout=90.0) as client:
        merged_prs = await _recent_merged_prs(
            client, owner=owner, repo=repo, headers=headers, max_prs=max_prs
        )

        for pr in merged_prs:
            pr_number = pr.get("number")
            if not isinstance(pr_number, int):
                continue

            reviews_url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
            for review in await _paginate(client, reviews_url, headers=headers, cap=100):
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

            comments_url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}/comments"
            for comment in await _paginate(client, comments_url, headers=headers, cap=200):
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

            issue_comments_url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{pr_number}/comments"
            for comment in await _paginate(client, issue_comments_url, headers=headers, cap=100):
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
            "You must browse merged PRs yourself with `GH_TOKEN=dummy gh` (reviews, "
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
