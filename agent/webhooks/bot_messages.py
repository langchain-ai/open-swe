"""Recognising the agent's own posts when reading a comment thread back.

Every channel that feeds a comment thread to the model has to drop the agent's
own messages first, or the run reads its own output as user input. The bot has
no per-channel identity to check against on every surface, so the marker is the
opening line the host writes — kept here once because a prefix that only one
channel knows about is a prefix that silently stops filtering on the others.
"""

BOT_MESSAGE_PREFIXES = (
    "🔐 **GitHub Authentication Required**",
    "✅ **Pull Request Created**",
    "✅ **Pull Request Updated**",
    "**Pull Request Created**",
    "**Pull Request Updated**",
    "🤖 **Agent Response**",
    "❌ **Agent Error**",
)


def is_own_bot_message(body: str) -> bool:
    """Whether ``body`` is one of the agent's own host-formatted messages."""
    return body.startswith(BOT_MESSAGE_PREFIXES)
