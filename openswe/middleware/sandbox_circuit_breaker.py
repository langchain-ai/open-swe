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
    handed_to_mda: bool = False,
    reason: str | None = None,
) -> str:
    """User-facing text for a sandbox that stopped answering.

    Deliberately does not claim the sandbox is gone for good — all we observed is
    that it stopped responding, and it may come back. ``handed_to_mda`` is for a
    box Open SWE set aside (deleted, or a reviewer's that stopped answering), where
    the thread's next run starts in a fresh one and "will not start a replacement"
    would be untrue.
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
    if reason:
        return warning(
            f"Open SWE could not open this thread's sandbox{which}: `{reason.replace('`', "'")}`. "
            "Retrigger this thread to try again."
        )
    if handed_to_mda:
        return warning(
            f"This thread's sandbox{which} is gone or stopped responding, so this run "
            "had nowhere to work. Its next run starts in a fresh sandbox, without "
            "anything that was not pushed. Retrigger this thread to continue."
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
    handed_to_mda: bool = False,
    reason: str | None = None,
) -> None:
    message = sandbox_unreachable_message(
        sandbox_id=sandbox_id,
        sandbox_name=sandbox_name,
        handed_to_mda=handed_to_mda,
        reason=reason,
    )
    await notify_run_source(config, message, what="sandbox_unreachable")
