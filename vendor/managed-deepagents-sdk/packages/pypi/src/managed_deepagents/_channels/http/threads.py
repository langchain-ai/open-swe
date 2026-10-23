"""Stable thread IDs for provider conversations."""

from __future__ import annotations

import json
import uuid


def channel_thread_id(channel_name: str, key: str) -> str:
    """The thread for one provider conversation, such as an issue or a chat.

    The same channel and key always give the same UUID, so each message
    continues the conversation. The npm runtime derives the same IDs.
    """
    encoded = json.dumps([channel_name, key], ensure_ascii=False, separators=(",", ":"))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mda-thread:v1:{encoded}"))
