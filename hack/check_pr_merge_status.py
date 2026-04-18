"""Check merge status counts for PR URLs exported from LangGraph threads."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_INPUT_PATH = "pr_urls.json"
DEFAULT_CONCURRENCY = 20
GITHUB_API_VERSION = "2022-11-28"


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


@dataclass(frozen=True)
class PullRequestRef:
    owner: str
    repo: str
    number: int
    url: str


def parse_github_pr_url(pr_url: str) -> PullRequestRef:
    parsed_url = urlparse(pr_url)
    if parsed_url.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported PR URL scheme: {pr_url}")
    if parsed_url.netloc not in {"github.com", "www.github.com"}:
        raise ValueError(f"Unsupported PR URL host: {pr_url}")

    path_parts = [part for part in parsed_url.path.split("/") if part]
    if len(path_parts) < 4 or path_parts[2] != "pull":
        raise ValueError(f"Unsupported GitHub PR URL path: {pr_url}")

    try:
        number = int(path_parts[3])
    except ValueError as exc:
        raise ValueError(f"Invalid GitHub PR number in URL: {pr_url}") from exc

    return PullRequestRef(
        owner=path_parts[0],
        repo=path_parts[1],
        number=number,
        url=pr_url,
    )


def load_pr_urls(input_path: Path) -> list[str]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Expected {input_path} to contain a JSON array of PR URLs")

    unique_urls: list[str] = []
    seen_urls: set[str] = set()
    for item in payload:
        if not isinstance(item, str) or not item:
            raise ValueError(f"Expected every item in {input_path} to be a non-empty string")
        if item not in seen_urls:
            seen_urls.add(item)
            unique_urls.append(item)
    return unique_urls


def classify_pr_state(pr_payload: dict[str, Any]) -> str:
    if pr_payload.get("merged") or pr_payload.get("merged_at"):
        return "merged"

    state = pr_payload.get("state")
    if state == "open":
        return "open_or_draft"
    if state == "closed":
        return "closed"

    raise ValueError(f"Unsupported GitHub PR state: {state!r}")


async def _fetch_pr_state(
    http_client: httpx.AsyncClient,
    pr_ref: PullRequestRef,
    github_pat: str,
    semaphore: asyncio.Semaphore,
) -> str:
    headers = {
        "Authorization": f"Bearer {github_pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }

    async with semaphore:
        response = await http_client.get(
            f"https://api.github.com/repos/{pr_ref.owner}/{pr_ref.repo}/pulls/{pr_ref.number}",
            headers=headers,
        )

    if response.status_code != 200:  # noqa: PLR2004
        raise RuntimeError(
            f"GitHub API returned {response.status_code} for {pr_ref.url}: {response.text}"
        )

    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected GitHub API response for {pr_ref.url}")
    return classify_pr_state(payload)


async def summarize_pr_statuses(
    *,
    pr_urls: list[str],
    github_pat: str,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> dict[str, int]:
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        tasks = [
            _fetch_pr_state(http_client, parse_github_pr_url(pr_url), github_pat, semaphore)
            for pr_url in pr_urls
        ]
        states = await asyncio.gather(*tasks)

    return {
        "total_prs": len(pr_urls),
        "total_merged_prs": sum(1 for state in states if state == "merged"),
        "total_open_draft_prs": sum(1 for state in states if state == "open_or_draft"),
        "total_closed_prs": sum(1 for state in states if state == "closed"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check merge status for GitHub PR URLs.")
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT_PATH,
        help=f"Path to the input JSON file. Defaults to {DEFAULT_INPUT_PATH!r}.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Concurrent GitHub API requests. Defaults to {DEFAULT_CONCURRENCY}.",
    )
    return parser.parse_args()


def main() -> None:
    _load_dotenv_if_available()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    args = parse_args()
    github_pat = os.environ.get("GITHUB_PAT")
    if not github_pat:
        raise RuntimeError("GITHUB_PAT must be set")

    pr_urls = load_pr_urls(Path(args.input))
    summary = asyncio.run(
        summarize_pr_statuses(
            pr_urls=pr_urls,
            github_pat=github_pat,
            concurrency=args.concurrency,
        )
    )

    logger.info("total PRs: %d", summary["total_prs"])
    logger.info("total merged PRs: %d", summary["total_merged_prs"])
    logger.info("total open/draft PRs: %d", summary["total_open_draft_prs"])
    logger.info("total closed PRs: %d", summary["total_closed_prs"])


if __name__ == "__main__":
    main()
