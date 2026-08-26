#!/usr/bin/env python3
"""Scrape actionable PR context with the authenticated GitHub CLI."""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.dashboard.pull_request_context import (  # noqa: E402
    _CHECKS_QUERY,
    _REVIEWS_QUERY,
    _actionable_check,
    parse_pull_request_url,
)


def _graphql(query: str, owner: str, repo: str, number: int, cursor: str | None) -> dict[str, Any]:
    command = [
        "gh",
        "api",
        "graphql",
        "-f",
        f"query={query}",
        "-F",
        f"owner={owner}",
        "-F",
        f"repo={repo}",
        "-F",
        f"number={number}",
    ]
    if cursor:
        command.extend(["-F", f"cursor={cursor}"])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"]))
    return payload["data"]["repository"]["pullRequest"]


def _scan(owner: str, repo: str, number: int) -> dict[str, Any]:
    threads: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    cursor = None
    review_decision = None
    merge_state = None
    while True:
        pull = _graphql(_REVIEWS_QUERY, owner, repo, number, cursor)
        if cursor is None:
            review_decision = pull.get("reviewDecision")
            merge_state = pull.get("mergeStateStatus")
            reviews = [
                {
                    "author": (review.get("author") or {}).get("login"),
                    "body": review.get("body") or "",
                    "url": review.get("url"),
                }
                for review in pull["latestOpinionatedReviews"]["nodes"]
                if review.get("state") == "CHANGES_REQUESTED"
            ]
        connection = pull["reviewThreads"]
        for thread in connection["nodes"]:
            if thread.get("isResolved"):
                continue
            line = thread.get("line") or thread.get("originalLine")
            comments = thread["comments"]
            threads.append(
                {
                    "path": thread.get("path") or "",
                    "line": line,
                    "isOutdated": bool(thread.get("isOutdated")),
                    "commentsTruncated": comments["pageInfo"]["hasNextPage"],
                    "comments": [
                        {
                            "author": (comment.get("author") or {}).get("login"),
                            "body": comment.get("body") or "",
                            "url": comment.get("url"),
                        }
                        for comment in comments["nodes"]
                    ],
                }
            )
        if not connection["pageInfo"]["hasNextPage"]:
            break
        cursor = connection["pageInfo"]["endCursor"]

    checks: list[dict[str, Any]] = []
    cursor = None
    head_sha = None
    while True:
        pull = _graphql(_CHECKS_QUERY, owner, repo, number, cursor)
        commit_nodes = pull["commits"]["nodes"]
        if not commit_nodes:
            break
        commit = commit_nodes[0]["commit"]
        head_sha = commit.get("oid")
        rollup = commit.get("statusCheckRollup")
        if not rollup:
            break
        connection = rollup["contexts"]
        checks.extend(
            check for node in connection["nodes"] if (check := _actionable_check(node)) is not None
        )
        if not connection["pageInfo"]["hasNextPage"]:
            break
        cursor = connection["pageInfo"]["endCursor"]

    return {
        "repoFullName": f"{owner}/{repo}",
        "number": number,
        "url": f"https://github.com/{owner}/{repo}/pull/{number}",
        "headSha": head_sha,
        "mergeState": merge_state,
        "reviewDecision": review_decision,
        "checks": checks,
        "changesRequestedReviews": reviews,
        "unresolvedReviewThreads": threads,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pr_url", help="canonical https://github.com/OWNER/REPO/pull/NUMBER URL")
    args = parser.parse_args()
    parsed = parse_pull_request_url(args.pr_url)
    if parsed is None:
        parser.error("pr_url must be a canonical GitHub pull request URL")
    try:
        print(json.dumps(_scan(*parsed), indent=2))
    except (KeyError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Could not scan pull request: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
