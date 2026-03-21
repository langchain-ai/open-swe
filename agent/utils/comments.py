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


def format_comments_for_prompt(
    comments: Sequence[dict[str, Any]],
    header: str = "User comments:",
) -> str:
    """Format a list of Linear comments into a readable string for prompt injection.

    Produces a block of text suitable for embedding directly into a system or
    user prompt, with each comment attributed to its author and timestamp.

    Example output::

        User comments:
        - [alice@example.com | 2026-03-19T10:00:00Z]: Please also update the tests.
        - [bob@example.com | 2026-03-19T10:05:00Z]: And bump the version number.

    Args:
        comments: Sequence of Linear comment dicts, each expected to contain
                  ``body``, ``createdAt``, and optionally a nested ``user``
                  dict with an ``email`` field.
        header:   Introductory line prepended to the block. Defaults to
                  ``"User comments:"``.

    Returns:
        A formatted multi-line string, or an empty string if ``comments`` is
        empty.
    """
    if not comments:
        return ""

    lines: list[str] = [header]
    for comment in comments:
        body = comment.get("body", "").strip()
        timestamp = comment.get("createdAt", "unknown time")
        user = comment.get("user") or {}
        author = user.get("email") or user.get("name") or "unknown user"
        lines.append(f"- [{author} | {timestamp}]: {body}")

    return "\n".join(lines)
