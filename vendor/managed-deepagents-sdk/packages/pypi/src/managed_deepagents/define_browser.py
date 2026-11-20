""":func:`define_browser` — browser use for an agent's threads.

The CLI discovers a root ``browser`` export in ``browser.py``; MDA then gives
the agent a LangSmith browser and Stagehand's tools, as ``browser_run``,
``browser_snapshot`` and ``browser_screenshot``.

    # browser.py
    from managed_deepagents import define_browser

    browser = define_browser(scope="user")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["BrowserDefinition", "define_browser"]

_MIN_IDLE_TTL_S = 60
_MAX_IDLE_TTL_S = 86_400

#: What every managed browser tool's name starts with.
BROWSER_TOOL_PREFIX = "browser_"


@dataclass(frozen=True)
class BrowserDefinition:
    """The browser declaration exported from ``browser.py``."""

    #: ``"thread"``: a browser per thread. ``"user"``: one per signed-in user,
    #: shared by their threads, logins included.
    scope: Literal["thread", "user"] = "thread"
    #: Stop an idle browser after this long.
    idle_ttl_seconds: int = 600
    #: The only hosts the browser may reach, or ``None`` for any.
    allow_list: tuple[str, ...] | None = None


def define_browser(
    *,
    scope: Literal["thread", "user"] = "thread",
    idle_ttl_seconds: int = 600,
    allow_list: Sequence[str] | None = None,
) -> BrowserDefinition:
    """Give the agent a LangSmith browser.

    Args:
        scope: ``"thread"`` gives each thread its own browser; ``"user"`` shares
            one per signed-in user across their threads, logins included, with
            a window per thread. Runs with no person behind them, such as
            scheduled runs, get their thread's browser.
        idle_ttl_seconds: Stop a browser after this long idle (60 to 86,400).
        allow_list: The only hosts the browser may reach, as ``define_sandbox``'s
            ``access_control`` takes them, such as ``["vendor.com", "*.vendor.com"]``.
            The browser's own sandbox is held to them too, so recording needs
            ``pypi.org`` and ``files.pythonhosted.org`` for its packages.
    """
    if scope not in ("thread", "user"):
        msg = f'browser scope must be "thread" or "user", not {scope!r}'
        raise ValueError(msg)
    if not _MIN_IDLE_TTL_S <= idle_ttl_seconds <= _MAX_IDLE_TTL_S:
        msg = f"idle_ttl_seconds must be between {_MIN_IDLE_TTL_S} and {_MAX_IDLE_TTL_S}"
        raise ValueError(msg)
    if allow_list is not None and (
        isinstance(allow_list, str)
        or not allow_list
        or not all(isinstance(host, str) and host for host in allow_list)
    ):
        msg = "allow_list must be a non-empty list of hosts"
        raise ValueError(msg)
    hosts = tuple(allow_list) if allow_list is not None else None
    return BrowserDefinition(scope=scope, idle_ttl_seconds=idle_ttl_seconds, allow_list=hosts)
