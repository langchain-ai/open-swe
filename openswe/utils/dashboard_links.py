"""Shared builders for dashboard ("Open in Web") URLs."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import ClassVar
from urllib.parse import quote, unquote, urlsplit

from openswe.config import ENV
from openswe.utils.dashboard_ui import is_single_origin

# Set by the dashboard's server proxy (ui/server/backend-proxy.ts) to the origin the
# browser used, so a request that came in through an alias domain gets its sign-in
# and MCP URLs on that domain.
PUBLIC_ORIGIN_HEADER = "x-open-swe-public-origin"


def dashboard_base_url() -> str:
    """Public base URL of the dashboard frontend, or ``""`` when there is none.

    An explicit ``DASHBOARD_BASE_URL`` wins. Otherwise the dashboard lives on the
    backend's own origin when the backend serves it (a bundled build, or the Vite
    dev server behind ``DASHBOARD_DEV_SERVER_URL``), so ``LANGGRAPH_URL`` is the
    base; with neither there is no dashboard to link to.
    """
    explicit = ENV.DASHBOARD_BASE_URL.optional()
    if explicit:
        return explicit.rstrip("/")
    if is_single_origin():
        return ENV.LANGGRAPH_URL.get().rstrip("/")
    return ""


def dashboard_api_base_url() -> str:
    """Public URL browsers use for ``/dashboard/api/*``; the backend's own unless overridden."""
    return (ENV.DASHBOARD_API_BASE_URL.optional() or ENV.LANGGRAPH_URL.get()).rstrip("/")


def public_dashboard_api_base_url() -> str | None:
    """``dashboard_api_base_url()`` when it is a public HTTPS origin, as for a deployment.

    None in local development, where nothing outside this machine can reach it.
    """
    base = dashboard_api_base_url()
    parsed = urlsplit(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    return base


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


class PublicOrigin:
    """The origin a request is answered on: the configured one, or an alias of it.

    ``DASHBOARD_ALIAS_URLS`` lists other origins that serve this dashboard and its
    API. Each is a full alias: the dashboard's proxy serves the UI and forwards the
    API on it, so a request arriving through one is answered with URLs on that
    origin. ``PublicOriginMiddleware`` binds the current request's alias.
    """

    _current: ClassVar[ContextVar[str | None]] = ContextVar("request_alias_origin", default=None)

    @staticmethod
    def aliases() -> frozenset[str]:
        """The alias origins configured in ``DASHBOARD_ALIAS_URLS``, normalized."""
        return frozenset(
            origin
            for value in ENV.DASHBOARD_ALIAS_URLS.get().split(",")
            if (origin := _origin(value.strip())) is not None
        )

    @classmethod
    def all(cls) -> list[str]:
        """Every origin that serves this dashboard: the configured one first, then its aliases.

        The desktop app opens links on any of them as its own, whichever one it was
        pointed at.
        """
        primary = _origin(dashboard_base_url())
        return [
            *([primary] if primary is not None else []),
            *sorted(cls.aliases() - {primary}),
        ]

    @classmethod
    def alias(cls, claimed: str | None) -> str | None:
        """``claimed`` normalized, if it is one of the configured alias origins.

        The header that carries it can be set by anyone who reaches the backend
        directly, so it only ever selects among origins this deployment lists.
        """
        if not claimed:
            return None
        origin = _origin(claimed.strip())
        return origin if origin is not None and origin in cls.aliases() else None

    @classmethod
    @contextmanager
    def serving(cls, origin: str | None) -> Generator[None]:
        """Answer the current request as if ``origin`` (an alias, or None) were the base URL."""
        token = cls._current.set(origin)
        try:
            yield
        finally:
            cls._current.reset(token)

    @classmethod
    def current_alias(cls) -> str | None:
        """The alias origin the current request came in through, or None."""
        return cls._current.get()

    @classmethod
    def dashboard_base_url(cls) -> str:
        """``dashboard_base_url()``, or the alias origin the current request came in through.

        For URLs the browser follows within this request (sign-in redirects, the
        page to land on afterwards). Links sent elsewhere (Slack, GitHub, email)
        keep ``dashboard_base_url()``, so they don't depend on who triggered them.
        """
        return cls.current_alias() or dashboard_base_url()

    @classmethod
    def dashboard_api_base_url(cls) -> str:
        """``dashboard_api_base_url()``, or the alias origin the current request came in through."""
        return cls.current_alias() or dashboard_api_base_url()


def dashboard_is_same_origin() -> bool:
    """True when the dashboard is served from the API's own origin."""
    frontend = _origin(dashboard_base_url())
    return frontend is not None and frontend == _origin(dashboard_api_base_url())


def dashboard_thread_url(thread_id: str) -> str | None:
    """Build the dashboard thread URL for a given thread id."""
    base_url = dashboard_base_url()
    if not base_url or not thread_id:
        return None
    return f"{base_url}/agents/{quote(thread_id, safe='')}"


def _dashboard_origins() -> set[str]:
    configured = [dashboard_base_url(), *ENV.DASHBOARD_ALLOWED_ORIGINS.get().split(",")]
    origins = {origin for value in configured if (origin := _origin(value.strip())) is not None}
    return origins | PublicOrigin.aliases()


def dashboard_thread_id(locator: str) -> str | None:
    """Extract a thread id from a raw id or Open SWE dashboard URL."""
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


def dashboard_review_pr_url(locator: str) -> str | None:
    """Resolve a review URL on a configured dashboard origin to its canonical GitHub PR."""
    try:
        parsed = urlsplit(locator)
    except ValueError:
        return None
    if (
        parsed.username is not None
        or parsed.password is not None
        or _origin(locator) not in _dashboard_origins()
    ):
        return None
    segments = parsed.path.rstrip("/").split("/")
    if len(segments) != 6 or segments[:3] != ["", "agents", "reviews"]:
        return None
    owner, repo, number = segments[3:]
    if not owner or not repo or not number.isascii() or not number.isdecimal() or int(number) < 1:
        return None
    return f"https://github.com/{owner}/{repo}/pull/{int(number)}"


def dashboard_incident_url(incident_id: str) -> str | None:
    """Build the dashboard Incidents detail URL for an incident id."""
    base_url = dashboard_base_url()
    if not base_url or not incident_id:
        return None
    return f"{base_url}/incidents/{quote(incident_id, safe='')}"
