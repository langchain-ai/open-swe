"""Shared builders for dashboard ("Open in Web") URLs."""

from urllib.parse import quote, urlsplit

from agent.config import ENV
from agent.utils.dashboard_ui import dashboard_static_dir


def dashboard_base_url() -> str:
    """Public base URL of the dashboard frontend, or ``""`` when there is none.

    An explicit ``DASHBOARD_BASE_URL`` wins. Otherwise the dashboard lives on the
    backend's own origin when its build is bundled, so ``LANGGRAPH_URL`` is the
    base; with neither there is no dashboard to link to.
    """
    explicit = ENV.DASHBOARD_BASE_URL.optional()
    if explicit:
        return explicit.rstrip("/")
    if dashboard_static_dir() is not None:
        return ENV.LANGGRAPH_URL.get().rstrip("/")
    return ""


def dashboard_api_base_url() -> str:
    """Public URL browsers use for ``/dashboard/api/*``; the backend's own unless overridden."""
    return (ENV.DASHBOARD_API_BASE_URL.optional() or ENV.LANGGRAPH_URL.get()).rstrip("/")


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def dashboard_is_same_origin() -> bool:
    """True when the dashboard is served from the API's own origin."""
    frontend = dashboard_base_url()
    return bool(frontend) and _origin(frontend) == _origin(dashboard_api_base_url())


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
