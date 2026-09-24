"""Validate pull-request links before delivering agent messages."""

import logging
import re
from collections.abc import Iterable
from typing import Any

import httpx2

from agent.github.app import get_github_app_installation_token
from agent.slack.client import parse_github_pr_url
from agent.tools.open_pull_request import thread_pull_requests

logger = logging.getLogger(__name__)

_GITHUB_PR_URL = re.compile(r"https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+/pull/[1-9][0-9]*")
_GITHUB_API = "https://api.github.com"


def _unique_urls(messages: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for message in messages:
        for url in _GITHUB_PR_URL.findall(message):
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def _ledger_matches(url: str, records: Iterable[dict[str, Any]]) -> bool:
    ref = parse_github_pr_url(url)
    if ref is None:
        return False
    for record in records:
        recorded_url = record.get("url")
        recorded_ref = parse_github_pr_url(recorded_url) if isinstance(recorded_url, str) else None
        if recorded_ref and (
            recorded_ref.owner.lower() == ref.owner.lower()
            and recorded_ref.repo.lower() == ref.repo.lower()
            and recorded_ref.number == ref.number
        ):
            return True
        repo_full_name = record.get("repo_full_name")
        number = record.get("number")
        if (
            isinstance(repo_full_name, str)
            and "/" in repo_full_name
            and isinstance(number, int)
            and not isinstance(number, bool)
            and repo_full_name.lower() == f"{ref.owner}/{ref.repo}".lower()
            and number == ref.number
        ):
            return True
    return False


async def _github_pr_exists(url: str) -> bool:
    ref = parse_github_pr_url(url)
    if ref is None:
        return False
    try:
        token = await get_github_app_installation_token(log_errors=False)
    except Exception:
        logger.warning(
            "Failed to obtain GitHub access for pull request link",
            extra={"url": url},
            exc_info=True,
        )
        return False
    if not token:
        return False
    try:
        async with httpx2.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{_GITHUB_API}/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except Exception:
        logger.warning(
            "Failed to verify GitHub pull request link", extra={"url": url}, exc_info=True
        )
        return False
    return response.status_code == 200


async def unverified_pr_links(thread_id: str, message: str) -> list[str]:
    """Return GitHub pull-request URLs that cannot be verified."""
    urls = _unique_urls([message])
    if not urls:
        return []
    records = await thread_pull_requests(thread_id)
    unverified: list[str] = []
    for url in urls:
        if not _ledger_matches(url, records) and not await _github_pr_exists(url):
            unverified.append(url)
    return unverified


def pr_link_error(urls: Iterable[str]) -> dict[str, Any]:
    """Build the delivery error for unverified pull-request URLs."""
    listed = ", ".join(urls)
    return {
        "success": False,
        "error": f"Could not verify pull request URL(s): {listed}",
        "hint": (
            "Call open_pull_request and use its returned url, or restate the outcome without "
            "a PR link; never use a branch URL."
        ),
    }
