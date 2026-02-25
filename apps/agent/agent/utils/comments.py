"""Helpers for Linear comment processing."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def get_recent_comments(
    comments: Sequence[dict[str, Any]], bot_message_prefixes: Sequence[str]
) -> list[dict[str, Any]] | None:
    """Return user comments since the last agent response, or None if none.

    Args:
        comments: Linear issue comments.
        bot_message_prefixes: Prefixes that identify agent/bot responses.

    Returns:
        Chronological list of comments since the last agent response, or None.
    """
    if not comments:
        return None

    sorted_comments = sorted(
        comments,
        key=lambda comment: comment.get("createdAt", ""),
        reverse=True,
    )

    recent_user_comments: list[dict[str, Any]] = []
    for comment in sorted_comments:
        body = comment.get("body", "")
        if any(body.startswith(prefix) for prefix in bot_message_prefixes):
            break  # Everything after this is from before the last agent response
        recent_user_comments.append(comment)

    if not recent_user_comments:
        return None

    recent_user_comments.reverse()
    return recent_user_comments
