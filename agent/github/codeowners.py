"""Resolve CODEOWNERS entries for pull request files."""

import logging
import re
from collections.abc import Iterable

import httpx2

from agent.github.http import GITHUB_API_BASE, github_client, github_request

logger = logging.getLogger(__name__)

_CODEOWNERS_PATHS = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")
_MAX_FILES_PAGE = 100


def _glob_regex(pattern: str) -> re.Pattern[str]:
    expression = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                expression.append(".*")
                index += 2
            else:
                expression.append("[^/]*")
        elif char == "?":
            expression.append("[^/]")
        elif char == "[":
            end = pattern.find("]", index + 1)
            if end == -1:
                expression.append(r"\[")
            else:
                expression.append(pattern[index : end + 1])
                index = end
        else:
            expression.append(re.escape(char))
        index += 1
    return re.compile("^" + "".join(expression) + "$", re.IGNORECASE)


def _pattern_matches(pattern: str, filename: str) -> bool:
    anchored = pattern.startswith("/")
    pattern = pattern.removeprefix("/")
    directory = pattern.endswith("/")
    pattern = pattern.rstrip("/")
    if not pattern or pattern.startswith("!"):
        return False
    if "/" not in pattern:
        if anchored:
            return bool(_glob_regex(pattern).match(filename)) or (
                directory and filename.startswith(f"{pattern}/")
            )
        candidates = filename.split("/")
        return any(_glob_regex(pattern).match(part) for part in candidates) or (
            directory and any(filename.startswith(f"{part}/") for part in candidates[:-1])
        )
    if directory:
        return filename == pattern or filename.startswith(f"{pattern}/")
    return bool(_glob_regex(pattern).match(filename))


def parse_codeowners(content: str) -> list[tuple[str, tuple[str, ...]]]:
    """Parse CODEOWNERS rules into patterns and owners."""
    rules: list[tuple[str, tuple[str, ...]]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) > 1:
            rules.append((fields[0], tuple(fields[1:])))
    return rules


def codeowners_for_files(content: str, filenames: Iterable[str]) -> set[str]:
    """Return the owners from the last matching rule for each file."""
    owners: set[str] = set()
    rules = parse_codeowners(content)
    for filename in filenames:
        matched: tuple[str, ...] = ()
        for pattern, rule_owners in rules:
            if _pattern_matches(pattern, filename):
                matched = rule_owners
        owners.update(owner if owner.startswith("@") else f"@{owner}" for owner in matched)
    return owners


async def codeowners_for_pull_request(
    owner: str, repo: str, pr_number: int, token: str
) -> set[str]:
    """Fetch and resolve CODEOWNERS for a pull request's changed files."""
    try:
        async with github_client(token=token) as client:
            filenames: list[str] = []
            page = 1
            while True:
                response = await github_request(
                    client,
                    "GET",
                    f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/files",
                    params={"per_page": str(_MAX_FILES_PAGE), "page": str(page)},
                )
                if response.status_code != 200:
                    return set()
                payload = response.json()
                if not isinstance(payload, list):
                    return set()
                page_files = [
                    item["filename"]
                    for item in payload
                    if isinstance(item, dict) and isinstance(item.get("filename"), str)
                ]
                filenames.extend(page_files)
                if len(payload) < _MAX_FILES_PAGE:
                    break
                page += 1
            if not filenames:
                return set()
            for path in _CODEOWNERS_PATHS:
                response = await github_request(
                    client,
                    "GET",
                    f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}",
                )
                if response.status_code == 200:
                    return codeowners_for_files(response.text, filenames)
                if response.status_code != 404:
                    return set()
    except httpx2.HTTPError, ValueError, TypeError:
        logger.warning(
            "Could not resolve CODEOWNERS for pull request",
            extra={"repository": f"{owner}/{repo}", "pull_request": pr_number},
            exc_info=True,
        )
    return set()


async def login_is_codeowner(
    owner: str, repo: str, pr_number: int, login: str, token: str
) -> tuple[bool, set[str], bool]:
    """Return whether a login is an owner, plus owners and lookup failure state."""
    owners = await codeowners_for_pull_request(owner, repo, pr_number, token)
    normalized_login = login.removeprefix("@").casefold()
    if any(
        owner_handle.removeprefix("@").casefold() == normalized_login
        for owner_handle in owners
        if "/" not in owner_handle
    ):
        return True, owners, False
    team_handles = [handle.removeprefix("@").split("/", 1) for handle in owners if "/" in handle]
    if not team_handles:
        return False, owners, False
    lookup_failed = False
    async with github_client(token=token) as client:
        for team_owner, team_slug in team_handles:
            try:
                response = await github_request(
                    client,
                    "GET",
                    f"{GITHUB_API_BASE}/orgs/{team_owner}/teams/{team_slug}/memberships/{login}",
                )
            except httpx2.HTTPError:
                lookup_failed = True
                continue
            if response.status_code == 200:
                return True, owners, lookup_failed
            if response.status_code not in {404, 302}:
                lookup_failed = True
    return False, owners, lookup_failed
