"""Helpers for Linear comment processing."""

from collections.abc import Sequence

from agent.linear.schema import LinearComment

BOT_MESSAGE_PREFIXES: tuple[str, ...] = (
    "🔐 **GitHub Authentication Required**",
    "✅ **Pull Request Created**",
    "✅ **Pull Request Updated**",
    "**Pull Request Created**",
    "**Pull Request Updated**",
    "🤖 **Agent Response**",
    "❌ **Agent Error**",
)


def is_agent_message(body: str) -> bool:
    """Whether a comment body is one Open SWE posted itself."""
    return any(body.startswith(prefix) for prefix in BOT_MESSAGE_PREFIXES)


def get_recent_comments(comments: Sequence[LinearComment]) -> list[LinearComment]:
    """User comments since the last agent response, oldest first."""
    if not comments:
        return []

    recent: list[LinearComment] = []
    for comment in sorted(comments, key=lambda item: item.created_at or "", reverse=True):
        if is_agent_message(comment.body):
            break
        recent.append(comment)

    recent.reverse()
    return recent
