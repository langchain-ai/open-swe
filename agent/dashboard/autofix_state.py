"""Per-PR auto-fix opt-out, stored in the LangGraph Store.

Auto-fix is gated by the per-user ``auto_fix_ci`` profile flag.
On top of that, a single PR can be silenced with ``@open-swe autofix off`` (and
re-enabled with ``@open-swe autofix on``), mirroring Cursor's
``@cursor autofix off`` per-PR control. The toggle lives here rather than on the
agent thread so a disable command is honored even before any fix run exists.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from langgraph_sdk import get_client

logger = logging.getLogger(__name__)

AUTOFIX_PR_STATE_NAMESPACE: list[str] = ["autofix_pr_state"]


def _client():
    return get_client()


def _key(owner: str, repo: str, pr_number: int) -> str:
    return f"{owner.lower()}/{repo.lower()}#{pr_number}"


async def is_pr_autofix_disabled(owner: str, repo: str, pr_number: int) -> bool:
    """Return whether auto-fix has been turned off for a specific PR.

    Lookup errors propagate: an explicit opt-out is a safety control, so a
    store failure must abort automatic dispatch rather than read as opted-in.
    """
    item = await _client().store.get_item(AUTOFIX_PR_STATE_NAMESPACE, _key(owner, repo, pr_number))
    if item is None:
        return False
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return bool(value.get("disabled")) if isinstance(value, dict) else False


async def get_pr_autofix_cycle_count(owner: str, repo: str, pr_number: int) -> int:
    """Return the number of review auto-fix cycles dispatched for a PR."""
    item = await _client().store.get_item(
        AUTOFIX_PR_STATE_NAMESPACE,
        f"{_key(owner, repo, pr_number)}:review_autofix_cycles",
    )
    if item is None:
        return 0
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return int(value.get("cycle_count", 0)) if isinstance(value, dict) else 0


async def set_pr_autofix_disabled(owner: str, repo: str, pr_number: int, disabled: bool) -> None:
    """Persist the per-PR auto-fix opt-out flag."""
    await _client().store.put_item(
        AUTOFIX_PR_STATE_NAMESPACE,
        _key(owner, repo, pr_number),
        {"disabled": disabled, "updated_at": datetime.now(UTC).isoformat()},
    )


async def set_pr_autofix_cycle_count(
    owner: str, repo: str, pr_number: int, cycle_count: int
) -> None:
    """Persist the review auto-fix cycle count for a PR."""
    await _client().store.put_item(
        AUTOFIX_PR_STATE_NAMESPACE,
        f"{_key(owner, repo, pr_number)}:review_autofix_cycles",
        {"cycle_count": cycle_count, "updated_at": datetime.now(UTC).isoformat()},
    )
