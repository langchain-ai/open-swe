"""Channel contracts, runtime binding, and schedule delivery."""

from __future__ import annotations

from managed_deepagents._channels.assistant_text import extract_assistant_text
from managed_deepagents._channels.binding import (
    SLACK_BOT_TOKEN_ENV,
    resolve_runtime_channel,
)
from managed_deepagents._channels.runtime import create_runtime_channel
from managed_deepagents._channels.schedule_delivery import (
    create_schedule_delivery_middleware,
)
from managed_deepagents._channels.types import (
    ChannelError,
    normalize_channel_destination,
)

__all__ = [
    "SLACK_BOT_TOKEN_ENV",
    "ChannelError",
    "create_runtime_channel",
    "create_schedule_delivery_middleware",
    "extract_assistant_text",
    "normalize_channel_destination",
    "resolve_runtime_channel",
]
