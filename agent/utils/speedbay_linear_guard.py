"""Deterministic self-trigger guard for the Linear webhook (OPE-23).

SPEEDBAY org-layer file — upstream does not own it.

The agent posts its Linear replies with the same runtime ``LINEAR_API_KEY``
that can trigger runs, and comments authored with a plain API key arrive with
``botActor: null`` — the route's bot filter never catches them (verified live;
FORK.md § Linear trigger). Without this guard, the only things preventing a
self-trigger loop are upstream's known-prefix list (which does not match our
agent's free-form replies) and the model happening not to write ``@openswe``
in a reply. One quoted trigger instruction in a completion comment would spawn
runs from runs, each billing a full agent run.

The fix is the standard bot-loop defense: resolve the runtime key's own user
id once, and drop any webhook comment authored by that id.

Async on purpose: the guard runs inside the async webhook route, where
langgraph dev's blockbuster instrumentation raises ``BlockingError`` on any
sync socket call — a sync HTTP client here doesn't just degrade the event
loop, it *fails* and silently disables the guard (found live in the first
verification attempt).

Fail-open by design: if the viewer id cannot be resolved (key missing or
Linear unreachable), the guard reports "not self" so human comments are never
blocked by an outage — the pre-guard state was permanently fail-open anyway.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_LINEAR_GQL = "https://api.linear.app/graphql"

# Process-lifetime cache: (resolved-flag, viewer id). The runtime key does not
# rotate while the server is up; a restart clears it. Concurrent first calls
# may both fetch — same result, no lock needed.
_resolved = False
_cached_id: str | None = None


async def _viewer_id() -> str | None:
    """The runtime ``LINEAR_API_KEY``'s own Linear user id, or None."""
    global _resolved, _cached_id
    if _resolved:
        return _cached_id

    key = os.environ.get("LINEAR_API_KEY", "")
    if not key:
        logger.warning("self-trigger guard disabled: LINEAR_API_KEY is not set")
        _resolved = True
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _LINEAR_GQL,
                json={"query": "{ viewer { id } }"},
                headers={"Authorization": key, "Content-Type": "application/json"},
            )
        _cached_id = resp.json()["data"]["viewer"]["id"]
    except Exception:
        # Not cached: a transient Linear outage at boot shouldn't disable the
        # guard for the process lifetime — retry on the next delivery.
        logger.warning("self-trigger guard: could not resolve viewer id (will retry)", exc_info=True)
        return None
    _resolved = True
    logger.info("self-trigger guard active for Linear user %s", _cached_id)
    return _cached_id


async def is_self_comment(payload: dict[str, Any]) -> bool:
    """True when the webhook comment was authored by the runtime key itself.

    Checks both id carriers observed in real deliveries: top-level
    ``actor.id`` and ``data.userId``.
    """
    viewer = await _viewer_id()
    if viewer is None:
        return False
    author_ids = {
        (payload.get("actor") or {}).get("id"),
        (payload.get("data") or {}).get("userId"),
    }
    return viewer in author_ids
