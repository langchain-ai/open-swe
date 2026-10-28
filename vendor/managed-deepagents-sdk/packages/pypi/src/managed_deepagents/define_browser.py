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
from typing import Literal

__all__ = ["BrowserDefinition", "define_browser"]

_MIN_IDLE_TTL_S = 60
_MAX_IDLE_TTL_S = 86_400


@dataclass(frozen=True)
class BrowserDefinition:
    """The browser declaration exported from ``browser.py``."""

    #: ``"thread"``: a browser per thread. ``"user"``: one per signed-in user,
    #: shared by their threads, logins included.
    scope: Literal["thread", "user"] = "thread"
    #: Stop an idle browser after this long.
    idle_ttl_seconds: int = 600


def define_browser(
    *, scope: Literal["thread", "user"] = "thread", idle_ttl_seconds: int = 600
) -> BrowserDefinition:
    """Give the agent a LangSmith browser.

    Args:
        scope: ``"thread"`` gives each thread its own browser; ``"user"`` shares
            one per signed-in user across their threads, logins included, with
            a window per thread. Runs with no person behind them, such as
            scheduled runs, get their thread's browser.
        idle_ttl_seconds: Stop a browser after this long idle (60 to 86,400).
    """
    if scope not in ("thread", "user"):
        msg = f'browser scope must be "thread" or "user", not {scope!r}'
        raise ValueError(msg)
    if not _MIN_IDLE_TTL_S <= idle_ttl_seconds <= _MAX_IDLE_TTL_S:
        msg = f"idle_ttl_seconds must be between {_MIN_IDLE_TTL_S} and {_MAX_IDLE_TTL_S}"
        raise ValueError(msg)
    return BrowserDefinition(scope=scope, idle_ttl_seconds=idle_ttl_seconds)
