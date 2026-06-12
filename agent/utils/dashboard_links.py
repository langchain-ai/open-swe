"""Shared builders for dashboard ("Open in Web") URLs."""

import os
from urllib.parse import quote

_DEFAULT_DASHBOARD_BASE_URL = "https://openswe.vercel.app"


def _dashboard_base_url() -> str:
    return os.environ.get("DASHBOARD_BASE_URL", _DEFAULT_DASHBOARD_BASE_URL).strip().rstrip("/")


def dashboard_thread_url(thread_id: str) -> str | None:
    """Build the dashboard thread URL for a given thread id."""
    base_url = _dashboard_base_url()
    if not base_url or not thread_id:
        return None
    return f"{base_url}/agents/{quote(thread_id, safe='')}"


def dashboard_review_url(owner: str, repo: str, pr_number: int) -> str | None:
    """Build the dashboard review-detail URL for a PR."""
    base_url = _dashboard_base_url()
    if not base_url or not owner or not repo or not pr_number:
        return None
    return (
        f"{base_url}/agents/reviews/"
        f"{quote(owner, safe='')}/{quote(repo, safe='')}/{quote(str(pr_number), safe='')}"
    )
