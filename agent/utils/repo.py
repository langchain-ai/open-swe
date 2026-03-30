"""Utilities for extracting repository configuration from text."""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

_DEFAULT_REPO_OWNER = os.environ.get("DEFAULT_REPO_OWNER", "langchain-ai")


def _get_gitlab_base_url() -> str:
    return os.environ.get("GITLAB_URL", "").strip().rstrip("/")


def _split_owner_and_name(value: str, default_owner: str) -> tuple[str, str]:
    if "/" in value:
        owner, name = value.rsplit("/", 1)
    else:
        owner, name = default_owner, value
    return owner, name.removesuffix(".git")


def _extract_repo_from_known_url(text: str) -> tuple[str | None, str | None]:
    gitlab_host = ""
    gitlab_prefix = ""
    gitlab_base_url = _get_gitlab_base_url()
    if gitlab_base_url:
        parsed_gitlab = urlparse(gitlab_base_url)
        gitlab_host = parsed_gitlab.netloc.lower()
        gitlab_prefix = parsed_gitlab.path.strip("/")

    for match in re.finditer(r"https?://[^\s)]+", text):
        candidate = match.group(0).rstrip("/.,")
        parsed = urlparse(candidate)
        host = parsed.netloc.lower()
        path = parsed.path.strip("/")
        if not path:
            continue

        if host == "github.com":
            owner, name = _split_owner_and_name(path, _DEFAULT_REPO_OWNER)
            return owner, name

        if gitlab_host and host == gitlab_host:
            if gitlab_prefix and path.startswith(f"{gitlab_prefix}/"):
                path = path[len(gitlab_prefix) + 1 :]
            if path.count("/") >= 1:
                owner, name = _split_owner_and_name(path, _DEFAULT_REPO_OWNER)
                return owner, name

    return None, None


def extract_repo_from_text(text: str, default_owner: str | None = None) -> dict[str, str] | None:
    """Extract owner/name repo config from text containing repo: syntax or GitHub URLs.

    Checks for explicit ``repo:owner/name`` or ``repo owner/name`` first, then
    falls back to GitHub URL extraction.

    Returns:
        A dict with ``owner`` and ``name`` keys, or ``None`` if no repo found.
    """
    if default_owner is None:
        default_owner = _DEFAULT_REPO_OWNER
    owner: str | None = None
    name: str | None = None

    if "repo:" in text or "repo " in text:
        match = re.search(r"repo[: ]([a-zA-Z0-9_.\-/]+)", text)
        if match:
            value = match.group(1).rstrip("/")
            owner, name = _split_owner_and_name(value, default_owner)

    if not owner or not name:
        owner, name = _extract_repo_from_known_url(text)

    if owner and name:
        return {"owner": owner, "name": name}
    return None
