"""Utilities for extracting repository configuration from text."""

import re

from openswe.config import ENV

_DEFAULT_REPO_OWNER = ENV.DEFAULT_REPO_OWNER.get()
_REPO_MENTION = re.compile(r"repo[: ]([a-zA-Z0-9_.\-/]+)")
_EXPLICIT_REPO = re.compile(r"repo:([a-zA-Z0-9_.\-]+/[a-zA-Z0-9_.\-]+)")
# github.com itself, not gist.github.com or another subdomain.
_GITHUB_URL = re.compile(r"(?<![\w.-])(?:www\.)?github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)")
# First path segments that are GitHub's own pages and uploads, never an owner.
_NOT_OWNERS = frozenset(
    {
        "about",
        "account",
        "advisories",
        "apps",
        "assets",
        "codespaces",
        "collections",
        "enterprises",
        "features",
        "marketplace",
        "notifications",
        "orgs",
        "organizations",
        "settings",
        "sponsors",
        "topics",
        "user-attachments",
        "users",
    }
)


def extract_repo_from_text(
    text: str, default_owner: str | None = None, *, explicit_only: bool = False
) -> dict[str, str] | None:
    """Extract owner/name repo config from text containing repo: syntax or GitHub URLs.

    Checks for explicit ``repo:owner/name`` or ``repo owner/name`` first, then
    falls back to the first github.com link to a repository (not an uploaded
    image, a gist or one of GitHub's own pages). ``explicit_only`` accepts only
    ``repo:owner/name``, for prose written for people (an issue's description),
    where "repo <word>" is ordinary English rather than a choice.

    Returns:
        A dict with ``owner`` and ``name`` keys, or ``None`` if no repo found.
    """
    if default_owner is None:
        default_owner = _DEFAULT_REPO_OWNER
    owner: str | None = None
    name: str | None = None

    if "repo:" in text or "repo " in text:
        match = (_EXPLICIT_REPO if explicit_only else _REPO_MENTION).search(text)
        if match:
            value = match.group(1).rstrip("/")
            if "/" in value:
                owner, name = value.split("/", 1)
            else:
                owner = default_owner
                name = value

    if not owner or not name:
        for github_match in _GITHUB_URL.finditer(text):
            if github_match.group(1).lower() not in _NOT_OWNERS:
                owner, name = github_match.groups()
                break

    if owner and name:
        return {"owner": owner, "name": name}
    return None
