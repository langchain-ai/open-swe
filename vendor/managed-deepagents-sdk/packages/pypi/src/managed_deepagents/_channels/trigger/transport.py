"""Trigger text transport with a private bound route."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING

from managed_deepagents._channels.runtime import require_text_content

if TYPE_CHECKING:
    from managed_deepagents._channels.trigger.client import TriggerClientLike
    from managed_deepagents._channels.types import ChannelMessage, JsonValue, PostedChannelMessage


class TriggerTransport:
    def __init__(self, client: TriggerClientLike, *, action_id: str | None = None) -> None:
        self._client = client
        self._action_id = action_id

    async def post(
        self, *, message: ChannelMessage, target: JsonValue, new_thread: bool = False
    ) -> PostedChannelMessage:
        text = require_text_content(message)
        if not isinstance(target, Mapping):
            raise ValueError("invalid Trigger target")
        channel_id = target.get("channel_id")
        address = target.get("address")
        source_thread_key = target.get("source_thread_key")
        if (
            not isinstance(channel_id, str)
            or not isinstance(address, Mapping)
            or not isinstance(source_thread_key, str)
        ):
            raise ValueError("invalid Trigger target")
        address = dict(address)
        if new_thread:
            address.pop("thread_id", None)
        result = await self._client.post_action(
            channel_id,
            {
                "type": "post",
                "action_id": self._action_id if self._action_id is not None else str(uuid.uuid4()),
                "address": dict(address),
                **({"source_thread_key": source_thread_key} if not new_thread else {}),
                "message": {"text": text},
            },
        )
        return {"id": result["id"]}
