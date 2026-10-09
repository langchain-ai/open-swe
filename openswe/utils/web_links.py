"""Shared builders for web ("Open in Web") URLs."""

from urllib.parse import quote, unquote, urlsplit

from openswe.config import ENV
from openswe.utils.web_ui import is_single_origin


def web_base_url() -> str:
    """Public base URL of the web app frontend, or ``""`` when there is none.

    An explicit ``WEB_BASE_URL`` wins. Otherwise the web app lives on the
    backend's own origin when the backend serves it (a bundled build, or the Vite
    dev server behind ``WEB_DEV_SERVER_URL``), so ``LANGGRAPH_URL`` is the
    base; with neither there is no web to link to.
    """
    explicit = ENV.WEB_BASE_URL.optional()
    if explicit:
        return explicit.rstrip("/")
    if is_single_origin():
        return ENV.LANGGRAPH_URL.get().rstrip("/")
    return ""


def web_api_base_url() -> str:
    """Public URL browsers use for ``/api/*``; the backend's own unless overridden."""
    return (ENV.WEB_API_BASE_URL.optional() or ENV.LANGGRAPH_URL.get()).rstrip("/")


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


def web_is_same_origin() -> bool:
    """True when the web app is served from the API's own origin."""
    frontend = _origin(web_base_url())
    return frontend is not None and frontend == _origin(web_api_base_url())


def web_thread_url(thread_id: str) -> str | None:
    """Build the web app thread URL for a given thread id."""
    base_url = web_base_url()
    if not base_url or not thread_id:
        return None
    return f"{base_url}/agents/{quote(thread_id, safe='')}"


def _web_origins() -> set[str]:
    configured = [web_base_url(), *ENV.WEB_ALLOWED_ORIGINS.get().split(",")]
    return {origin for value in configured if (origin := _origin(value.strip())) is not None}


def web_thread_id(locator: str) -> str | None:
    """Extract a thread id from a raw id or Open SWE web URL."""
    value = locator.strip().strip("<>")
    if "|" in value:
        value = value.split("|", 1)[0]
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
        or _origin(value) not in _web_origins()
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


def web_plan_url(thread_id: str) -> str | None:
    """Build the web app plan-review URL for a given thread id."""
    base_url = web_base_url()
    if not base_url or not thread_id:
        return None
    return f"{base_url}/agents/{quote(thread_id, safe='')}/plan"


def web_workflow_approval_url(thread_id: str, fingerprint: str) -> str | None:
    """Build the web app workflow approval URL for a thread/fingerprint."""
    thread_url = web_thread_url(thread_id)
    if not thread_url or not fingerprint:
        return thread_url
    return f"{thread_url}?workflowApproval={quote(fingerprint, safe='')}"


def web_review_url(owner: str, repo: str, pr_number: int) -> str | None:
    """Build the web app review-detail URL for a PR."""
    base_url = web_base_url()
    if not base_url or not owner or not repo or not pr_number:
        return None
    return (
        f"{base_url}/agents/reviews/"
        f"{quote(owner, safe='')}/{quote(repo, safe='')}/{quote(str(pr_number), safe='')}"
    )


def web_incident_url(incident_id: str) -> str | None:
    """Build the web app Incidents detail URL for an incident id."""
    base_url = web_base_url()
    if not base_url or not incident_id:
        return None
    return f"{base_url}/incidents/{quote(incident_id, safe='')}"
