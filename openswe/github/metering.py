"""One log line per GitHub API request Robin's backend sends, saying what it cost and who sent it.

The App installation's hourly quota is shared by the backend, MDA, and every sandbox
whose proxy carries an installation token. ``openswe.github.quota`` samples the total
each minute; these lines break the backend's share down by caller and route, so a
quota incident can name what spent it instead of guessing.

Each line carries GitHub's own accounting for the request (``x-ratelimit-resource``
and ``x-ratelimit-used``) and the kind of token sent, read from its prefix alone:
an installation token spends the shared quota, a person's token spends their own.
They also carry how long GitHub took, the installation an installation token belongs
to, and the graph run that sent the request, so a burst can be traced to its thread.
A request that fails before GitHub answers is logged with its error type instead,
and one the quota gate refused itself, while the installation's quota is spent, is
logged as held.
"""

import inspect
import logging
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final

import httpx2

from openswe.run_config import RunConfig

logger = logging.getLogger(__name__)

_GITHUB_API_HOST: Final = "api.github.com"
# Set on a response replayed from a 304: GitHub spent no quota on it.
REVALIDATED: Final = "openswe_github_revalidated"
# Set on a refusal ``QuotaGate`` answered itself: GitHub never saw the request.
HELD: Final = "openswe_github_held"
# Set on a request sent with an App installation's token: the installation it belongs to.
INSTALLATION: Final = "openswe_github_installation_id"
# Modules between a caller and the wire; the caller is the first frame outside them.
# ``http`` and ``pull_request_status`` hold the named client methods (``RepoClient``,
# ``PullRequestClient``) that all other code must call GitHub through, so a frame in
# them names the method, not the code that wanted the request; so does ``ci``, whose
# readers (``CommitChecks``, ``RequiredCheck``) serve baby-sitting and expedited review.
_PLUMBING: Final = frozenset(
    {
        __name__,
        "openswe.github.ci",
        "openswe.github.http",
        "openswe.github.pull_request_status",
        "openswe.github.revalidation",
        "openswe.github.sdk",
    }
)
_MAX_FRAMES: Final = 64
# Documented prefixes of GitHub's token formats, longest first.
_TOKEN_KINDS: Final = (
    ("github_pat_", "pat"),
    ("ghs_", "installation"),
    ("ghu_", "user"),
    ("gho_", "user"),
    ("ghp_", "pat"),
    ("eyJ", "app_jwt"),
)
# A commit SHA as a whole path segment or either side of a compare's ``base...head``.
_SHA: Final = re.compile(r"(?:(?<=/)|(?<=\.\.\.))[0-9a-f]{40}(?=/|$|\.\.\.)")
_NUMBER: Final = re.compile(r"/\d+(?=/|$)")
# The caller captured where the client layer fans out into tasks, whose stacks start there.
_CALLER: ContextVar[str | None] = ContextVar("github_caller", default=None)


class MeteredTransport(httpx2.AsyncBaseTransport):
    """Logs each REST or GraphQL request to GitHub's API with its cost and caller.

    Wraps the revalidating transport, so a replayed ``304`` shows as revalidated:
    GitHub doesn't count those against the quota.
    """

    def __init__(self, inner: httpx2.AsyncBaseTransport) -> None:
        self.inner = inner

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host != _GITHUB_API_HOST:
            return await self.inner.handle_async_request(request)
        sender = self.caller()
        started = time.monotonic()
        try:
            response = await self.inner.handle_async_request(request)
        except Exception as exc:
            self._meter(request, None, sender, started, type(exc).__name__)
            raise
        self._meter(request, response, sender, started)
        return response

    @classmethod
    def _meter(
        cls,
        request: httpx2.Request,
        response: httpx2.Response | None,
        sender: str,
        started: float,
        error: str | None = None,
    ) -> None:
        try:
            cls._log(request, response, sender, time.monotonic() - started, error)
        except Exception:
            # Metering must never break the request it describes.
            logger.debug("Could not meter GitHub request", exc_info=True)

    async def aclose(self) -> None:
        await self.inner.aclose()

    @staticmethod
    def caller() -> str:
        """The ``module.function`` in Robin that asked for this request, or ``unknown``.

        An awaited coroutine chain is on the stack while it runs, so the frames above the
        transport lead back to the code that started the request. A task started by
        ``asyncio.gather`` has only its own frames; ``attributed`` carries the caller there.
        """
        here = inspect.currentframe()
        frame = here.f_back if here is not None else None
        for _ in range(_MAX_FRAMES):
            if frame is None:
                break
            module = frame.f_globals.get("__name__", "")
            if module.startswith("openswe.") and module not in _PLUMBING:
                return f"{module}.{frame.f_code.co_qualname}"
            frame = frame.f_back
        return _CALLER.get() or "unknown"

    @staticmethod
    @contextmanager
    def attributed() -> Iterator[None]:
        """Keep the current caller for the tasks the client layer starts inside this block."""
        if _CALLER.get() is not None:
            yield
            return
        token = _CALLER.set(MeteredTransport.caller())
        try:
            yield
        finally:
            _CALLER.reset(token)

    @staticmethod
    def route(url: httpx2.URL) -> str:
        """``url``'s path with commit SHAs and numeric IDs folded, so one endpoint groups as one."""
        return _NUMBER.sub("/:n", _SHA.sub(":sha", url.path))

    @staticmethod
    def token_kind(authorization: str | None) -> str:
        """Which quota a request spends, from its token's prefix; the token itself is never kept."""
        if not authorization:
            return "anonymous"
        _, _, token = authorization.partition(" ")
        token = token or authorization
        for prefix, kind in _TOKEN_KINDS:
            if token.startswith(prefix):
                return kind
        return "other"

    @staticmethod
    async def tag_installation(installation_id: int, request: httpx2.Request) -> None:
        """A request hook naming the App installation whose token the client sends."""
        request.extensions[INSTALLATION] = installation_id

    @staticmethod
    def _run_ids() -> dict[str, str]:
        """The graph thread, run and invocation sending the request, when there is one."""
        try:
            config = RunConfig.from_runtime()
        except RuntimeError:
            logger.debug("GitHub request has no graph runtime context")
            return {}
        ids = {
            "thread_id": config.thread_id,
            "run_id": config.run_id,
            "invocation_id": config.invocation_id,
        }
        return {name: value for name, value in ids.items() if value is not None}

    @staticmethod
    def _header_int(headers: httpx2.Headers, name: str) -> int | None:
        try:
            return int(headers[name])
        except KeyError, ValueError:
            return None

    @classmethod
    def _log(
        cls,
        request: httpx2.Request,
        response: httpx2.Response | None,
        sender: str,
        duration: float,
        error: str | None,
    ) -> None:
        token_kind = cls.token_kind(request.headers.get("Authorization"))
        fields: dict[str, object] = {
            "github_caller": sender,
            "github_method": request.method,
            "github_route": cls.route(request.url),
            "github_token_kind": token_kind,
            "github_duration_ms": round(duration * 1000, 1),
            **cls._run_ids(),
        }
        # Only an installation token spends an installation's quota; a local ``gh`` token
        # standing in for an unconfigured App is a person's, whatever the client was opened as.
        installation = request.extensions.get(INSTALLATION)
        if token_kind == "installation" and isinstance(installation, int):
            fields["github_installation_id"] = installation
        if response is None:
            logger.info("GitHub API request", extra={**fields, "github_error_type": error})
            return
        headers = response.headers
        revalidated = response.status_code == 304 or bool(response.extensions.get(REVALIDATED))
        logger.info(
            "GitHub API request",
            extra={
                **fields,
                "github_status": response.status_code,
                "github_revalidated": revalidated,
                "github_held": bool(response.extensions.get(HELD)),
                "github_quota_resource": headers.get("x-ratelimit-resource"),
                "github_quota_used": cls._header_int(headers, "x-ratelimit-used"),
                "github_quota_remaining": cls._header_int(headers, "x-ratelimit-remaining"),
                "github_quota_limit": cls._header_int(headers, "x-ratelimit-limit"),
                "github_quota_reset": cls._header_int(headers, "x-ratelimit-reset"),
                "github_retry_after": cls._header_int(headers, "retry-after"),
                "github_request_id": headers.get("x-github-request-id", "")[:128],
                "github_accepted_permissions": headers.get("x-accepted-github-permissions", "")[
                    :512
                ],
            },
        )
