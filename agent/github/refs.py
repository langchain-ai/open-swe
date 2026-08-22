"""GitHub references — pull requests and repositories — parsed out of free text."""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from ..config import default_repo_owner


@dataclass(frozen=True)
class GitHubPrRef:
    owner: str
    repo: str
    number: int
    url: str


def parse_github_pr_url(url: str) -> GitHubPrRef | None:
    """Parse a GitHub PR URL, tolerating Slack's ``<url|label>`` link markup."""
    cleaned_url = url.strip().strip("<>")
    if "|" in cleaned_url:
        cleaned_url = cleaned_url.split("|", 1)[0]

    parsed = urlparse(cleaned_url)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return None

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 4 or path_parts[2] != "pull":
        return None

    try:
        number = int(path_parts[3])
    except ValueError:
        return None

    owner = path_parts[0]
    repo = path_parts[1]
    return GitHubPrRef(
        owner=owner,
        repo=repo,
        number=number,
        url=f"https://github.com/{owner}/{repo}/pull/{number}",
    )


def extract_repo_from_text(text: str, default_owner: str | None = None) -> dict[str, str] | None:
    """Extract owner/name repo config from text containing repo: syntax or GitHub URLs.

    Checks for explicit ``repo:owner/name`` or ``repo owner/name`` first, then
    falls back to GitHub URL extraction.

    Returns:
        A dict with ``owner`` and ``name`` keys, or ``None`` if no repo found.
    """
    if default_owner is None:
        default_owner = default_repo_owner()
    owner: str | None = None
    name: str | None = None

    if "repo:" in text or "repo " in text:
        match = re.search(r"repo[: ]([a-zA-Z0-9_.\-/]+)", text)
        if match:
            value = match.group(1).rstrip("/")
            if "/" in value:
                owner, name = value.split("/", 1)
            else:
                owner = default_owner
                name = value

    if not owner or not name:
        github_match = re.search(r"github\.com/([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)", text)
        if github_match:
            owner, name = github_match.group(1).split("/", 1)

    if owner and name:
        return {"owner": owner, "name": name}
    return None
