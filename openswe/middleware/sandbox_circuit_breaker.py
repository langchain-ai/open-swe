"""Telling the user, on the channel they triggered from, that their sandbox went quiet."""

import re
from collections.abc import Mapping
from typing import Any

from openswe.run_source import notify_run_source
from openswe.utils.user_messages import warning


def sandbox_unreachable_message(
    *,
    sandbox_id: str | None = None,
    sandbox_name: str | None = None,
    replacement_attempted: bool = False,
) -> str:
    """User-facing text for a sandbox that stopped answering.

    Deliberately does not claim the sandbox is gone for good — all we observed is
    that it stopped responding, and it may come back. ``replacement_attempted``
    is for callers allowed to replace an unreachable sandbox (the read-only
    reviewer), where "Open SWE will not start a replacement" would be untrue.
    """
    identifiers = [
        part
        for part in (
            f"name {sandbox_name}" if sandbox_name else None,
            f"id {sandbox_id}" if sandbox_id else None,
        )
        if part
    ]
    which = f" ({', '.join(identifiers)})" if identifiers else ""
    if replacement_attempted:
        return warning(
            f"This thread's sandbox{which} stopped responding and Open SWE could "
            "not provision a replacement, so this run had nowhere to work. "
            "Retrigger this thread to try again."
        )
    return warning(
        f"This thread's sandbox{which} stopped responding, and Open SWE can't tell "
        "whether it will come back. Open SWE will not start a replacement on its "
        "own: a new sandbox is empty, so swapping one in would throw away anything "
        "not yet committed and pushed while still looking like a recovery. "
        "Retrigger this thread to try the same sandbox again, or start a new thread "
        "to get a fresh one."
    )


_SANDBOX_ID_RE = re.compile(r"\bsb-[A-Za-z0-9-]+\b")


def extract_sandbox_id(text: str) -> str | None:
    match = _SANDBOX_ID_RE.search(text)
    return match.group(0) if match else None


async def post_sandbox_unreachable_notification(
    config: Mapping[str, Any],
    *,
    sandbox_id: str | None = None,
    sandbox_name: str | None = None,
    replacement_attempted: bool = False,
) -> None:
    message = sandbox_unreachable_message(
        sandbox_id=sandbox_id,
        sandbox_name=sandbox_name,
        replacement_attempted=replacement_attempted,
    )
    await notify_run_source(config, message, what="sandbox_unreachable")
