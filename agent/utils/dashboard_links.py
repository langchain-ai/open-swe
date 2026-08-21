"""Shared builders for dashboard ("Open in Web") URLs."""

from urllib.parse import quote

from ..config import dashboard_base_url

# Dashboard route where users manage their GitHub↔Slack link.
PROFILE_SETTINGS_PATH = "/my-settings"


def build_settings_url() -> str | None:
    """Return the dashboard Profile Settings URL, or ``None`` if not configured.

    This is a plain, token-free link: it carries no per-user identity, so it is
    safe to share in a public Slack thread. The user signs in with GitHub from
    their own session and connects Slack via verified OIDC on the settings page.
    """
    frontend_base = dashboard_base_url()
    if not frontend_base:
        return None
    return f"{frontend_base}{PROFILE_SETTINGS_PATH}"


def dashboard_thread_url(thread_id: str) -> str | None:
    """Build the dashboard thread URL for a given thread id."""
    base_url = dashboard_base_url()
    if not base_url or not thread_id:
        return None
    return f"{base_url}/agents/{quote(thread_id, safe='')}"


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
