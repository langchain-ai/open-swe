"""Open a rollout page in the sandbox browser.

The CUA CLI (``cua``) drives the Chrome desktop in the sandbox. This tool opens
the page when that CLI is installed. It does not log in. ``ROLLOUT_BOT_EMAIL``
and ``ROLLOUT_BOT_PASSWORD`` are never sent to the sandbox, returned, or logged.
"""

import logging
import shlex
from typing import Any
from urllib.parse import urlparse

from agent.run_config import RunConfig
from agent.sandboxes.state import get_sandbox_backend
from agent.utils.url_safety import resolve_and_validate

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_SECONDS = 15
_OPEN_TIMEOUT_SECONDS = 60


def _invalid() -> dict[str, Any]:
    return {"ok": False, "reason": "invalid_url"}


def _unavailable() -> dict[str, Any]:
    return {"ok": False, "reason": "browser_unavailable"}


def _https_page(url: str) -> str | None:
    parsed = urlparse(url.strip())
    if parsed.username or parsed.password or parsed.scheme != "https" or not parsed.hostname:
        return None
    safe, _reason, _hostname, _addresses = resolve_and_validate(parsed.geturl())
    if not safe:
        return None
    return parsed.geturl()


async def rollout_page_check(url: str, expected: str = "") -> dict[str, Any]:
    """Implement the `rollout_page_check` tool."""
    del expected
    page = _https_page(url)
    if page is None:
        return _invalid()
    try:
        thread_id = RunConfig.from_runtime().thread_id
        if not isinstance(thread_id, str) or not thread_id:
            return _unavailable()
        backend = await get_sandbox_backend(thread_id)
        probe = await backend.aexecute("command -v cua", timeout=_PROBE_TIMEOUT_SECONDS)
        if probe.exit_code != 0 or not str(probe.output).strip():
            return _unavailable()
        opened = await backend.aexecute(
            f"cua do open {shlex.quote(page)}",
            timeout=_OPEN_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.warning("Rollout page check could not use the sandbox browser", exc_info=True)
        return _unavailable()
    if opened.exit_code != 0:
        return _unavailable()
    return {"ok": True, "reason": "browser_opened"}
