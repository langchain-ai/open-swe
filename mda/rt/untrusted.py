"""Fencing someone else's words in a run's prompt, as the backend does.

The agent's system prompt tells it to read what sits between these tags as data,
never as instructions. A copy of ``openswe.github.comments.fence_github_comment_body``
(AGENTS.md's exception for channel input), standard library only, held byte-identical
to it by ``tests/agent/test_mda_untrusted.py``.
"""

from typing import Final

OPEN_TAG: Final = "<dangerous-external-untrusted-users-comment>"
CLOSE_TAG: Final = "</dangerous-external-untrusted-users-comment>"
_SANITIZED_OPEN_TAG: Final = "[blocked-untrusted-comment-tag-open]"
_SANITIZED_CLOSE_TAG: Final = "[blocked-untrusted-comment-tag-close]"


def fence(body: str, *, registered: bool) -> str:
    """``body`` without the reserved tags, fenced as untrusted unless its author is an
    Open SWE user."""
    sanitized = body.replace(OPEN_TAG, _SANITIZED_OPEN_TAG).replace(CLOSE_TAG, _SANITIZED_CLOSE_TAG)
    if registered:
        return sanitized
    return f"{OPEN_TAG}\n{sanitized}\n{CLOSE_TAG}"
