"""Cache every Corridor-reviewed PR's reviews, comments, and commits from GitHub.

Usage:
    uv run python -m evals.corridor_gaps.collect --since 2026-06-29
"""

import argparse
import asyncio
import logging

from pydantic import BaseModel, TypeAdapter

from evals.corridor_gaps.github import (
    CORRIDOR_LOGIN,
    OSWE_LOGIN,
    REPO,
    Commit,
    PrBundle,
    PullRequest,
    Review,
    ReviewComment,
    bundle_path,
    gh_json,
    gh_list,
)

logger = logging.getLogger(__name__)


class _SearchItem(BaseModel):
    number: int


class _SearchPage(BaseModel):
    items: list[_SearchItem]


class _MergeBase(BaseModel):
    sha: str


class _Compare(BaseModel):
    merge_base_commit: _MergeBase


async def corridor_pr_numbers(since: str) -> list[int]:
    query = f"repo:{REPO} is:pr created:>={since} reviewed-by:app/corridor-security"
    pages = await gh_json(
        "api",
        "-X",
        "GET",
        "search/issues",
        "--paginate",
        "--slurp",
        "-f",
        f"q={query}",
        "-f",
        "per_page=100",
    )
    parsed = TypeAdapter(list[_SearchPage]).validate_python(pages)
    return sorted({item.number for page in parsed for item in page.items})


async def merge_base(base_ref: str, sha: str) -> str | None:
    try:
        raw = await gh_json(
            "api", f"repos/{REPO}/compare/{base_ref}...{sha}", "-q", "{merge_base_commit}"
        )
    except RuntimeError:
        logger.warning("merge base unavailable", extra={"sha": sha})
        return None
    return _Compare.model_validate(raw).merge_base_commit.sha


async def collect_pr(number: int, refresh: bool) -> None:
    path = bundle_path(number)
    if path.exists() and not refresh:
        return
    pr_raw, reviews, comments, commits = await asyncio.gather(
        gh_json("api", f"repos/{REPO}/pulls/{number}"),
        gh_list(f"repos/{REPO}/pulls/{number}/reviews", Review),
        gh_list(f"repos/{REPO}/pulls/{number}/comments", ReviewComment),
        gh_list(f"repos/{REPO}/pulls/{number}/commits", Commit),
    )
    pr = PullRequest.model_validate(pr_raw)
    bot_shas = {
        c.original_commit_id
        for c in comments
        if c.user and c.user.login in (CORRIDOR_LOGIN, OSWE_LOGIN)
    } | {r.commit_id for r in reviews if r.commit_id and r.user and r.user.login == CORRIDOR_LOGIN}
    shas = sorted(bot_shas)
    bases = await asyncio.gather(*(merge_base(pr.base.ref, sha) for sha in shas))
    bundle = PrBundle(
        pr=pr,
        reviews=reviews,
        comments=comments,
        commits=commits,
        merge_bases=dict(zip(shas, bases, strict=True)),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bundle.model_dump_json(indent=1))
    print(f"  #{number} {pr.title}: {len(reviews)} reviews, {len(comments)} comments")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="PR creation date lower bound, YYYY-MM-DD")
    ap.add_argument("--refresh", action="store_true", help="Refetch PRs already cached")
    args = ap.parse_args()
    numbers = await corridor_pr_numbers(args.since)
    print(f"{len(numbers)} Corridor-reviewed PRs since {args.since}")
    await asyncio.gather(*(collect_pr(n, args.refresh) for n in numbers))


if __name__ == "__main__":
    asyncio.run(main())
