"""Per-PR auto-fix opt-out, stored in the LangGraph Store.

Auto-fix is gated by the per-user ``auto_fix_ci`` profile flag.
On top of that, a single PR can be silenced with ``@open-swe autofix off`` (and
re-enabled with ``@open-swe autofix on``), mirroring Cursor's
``@cursor autofix off`` per-PR control. The toggle lives here rather than on the
agent thread so a disable command is honored even before any fix run exists.
"""

import logging

from ..store import get_value, now_iso, put_value

logger = logging.getLogger(__name__)

AUTOFIX_PR_STATE_NAMESPACE: list[str] = ["autofix_pr_state"]


def _key(owner: str, repo: str, pr_number: int) -> str:
    return f"{owner.lower()}/{repo.lower()}#{pr_number}"


async def is_pr_autofix_disabled(owner: str, repo: str, pr_number: int) -> bool:
    """Return whether auto-fix has been turned off for a specific PR.

    Fail-soft on purpose: this runs inside CI-failure webhook handling, and an
    unreachable store must leave auto-fix at its configured default rather than
    break the handler.
    """
    try:
        record = await get_value(AUTOFIX_PR_STATE_NAMESPACE, _key(owner, repo, pr_number))
    except Exception:
        logger.warning("autofix PR state lookup failed", exc_info=True)
        return False
    return bool(record.get("disabled")) if record else False


async def set_pr_autofix_disabled(owner: str, repo: str, pr_number: int, disabled: bool) -> None:
    """Persist the per-PR auto-fix opt-out flag."""
    await put_value(
        AUTOFIX_PR_STATE_NAMESPACE,
        _key(owner, repo, pr_number),
        {"disabled": disabled, "updated_at": now_iso()},
    )
