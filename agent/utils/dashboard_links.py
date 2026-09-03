"""Shared builders for dashboard ("Open in Web") URLs."""

import os
from urllib.parse import quote, unquote, urlsplit

_DEFAULT_DASHBOARD_BASE_URL = "https://openswe.vercel.app"


def dashboard_base_url() -> str:
    """Return the configured dashboard base URL."""
    return os.environ.get("DASHBOARD_BASE_URL", _DEFAULT_DASHBOARD_BASE_URL).strip().rstrip("/")


def dashboard_thread_url(thread_id: str) -> str | None:
    """Build the dashboard thread URL for a given thread id."""
    base_url = dashboard_base_url()
    if not base_url or not thread_id:
        return None
    return f"{base_url}/agents/{quote(thread_id, safe='')}"


def _origin(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    default_port = 443 if parsed.scheme == "https" else 80
    suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{parsed.scheme.lower()}://{parsed.hostname.lower()}{suffix}"


def _dashboard_origins() -> set[str]:
    configured = [dashboard_base_url(), *os.environ.get("DASHBOARD_ALLOWED_ORIGINS", "").split(",")]
    return {origin for value in configured if (origin := _origin(value.strip())) is not None}


def dashboard_thread_id(locator: str) -> str | None:
    """Extract a thread id from a raw id or Open SWE dashboard URL."""
    value = locator.strip()
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        has_credentials = parsed.username is not None or parsed.password is not None
    except ValueError:
        return None
    if not parsed.scheme and not parsed.netloc:
        return value if "/" not in value and "?" not in value and "#" not in value else None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or has_credentials
        or _origin(value) not in _dashboard_origins()
    ):
        return None
    segments = parsed.path.split("/")
    if len(segments) not in {3, 4} or segments[:2] != ["", "agents"]:
        return None
    if len(segments) == 4 and segments[3] != "plan":
        return None
    try:
        thread_id = unquote(segments[2], errors="strict")
    except UnicodeDecodeError:
        return None
    if not thread_id or quote(thread_id, safe="") != segments[2] or "/" in thread_id:
        return None
    return thread_id


def dashboard_plan_url(thread_id: str) -> str | None:
    """Build the dashboard plan-review URL for a given thread id."""
    base_url = dashboard_base_url()
    if not base_url or not thread_id:
        return None
    return f"{base_url}/agents/{quote(thread_id, safe='')}/plan"


def dashboard_workflow_approval_url(thread_id: str, fingerprint: str) -> str | None:
    """Build the dashboard workflow approval URL for a thread/fingerprint."""
    thread_url = dashboard_thread_url(thread_id)
    if not thread_url or not fingerprint:
        return thread_url
    return f"{thread_url}?workflowApproval={quote(fingerprint, safe='')}"


def dashboard_review_url(owner: str, repo: str, pr_number: int) -> str | None:
    """Build the dashboard review-detail URL for a PR."""
    base_url = dashboard_base_url()
    if not base_url or not owner or not repo or not pr_number:
        return None
    return (
        f"{base_url}/agents/reviews/"
        f"{quote(owner, safe='')}/{quote(repo, safe='')}/{quote(str(pr_number), safe='')}"
    )
