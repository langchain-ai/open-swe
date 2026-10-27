"""Trigger text transport with a private bound route."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, TypedDict

from managed_deepagents._channels.runtime import require_text_content

if TYPE_CHECKING:
    from managed_deepagents._channels.trigger.client import TriggerClientLike
    from managed_deepagents._channels.trigger.types import TriggerStartedThread
    from managed_deepagents._channels.types import ChannelMessage, JsonValue, PostedChannelMessage


class TriggerTransport:
    def __init__(self, client: TriggerClientLike, *, action_id: str | None = None) -> None:
        self._client = client
        self._action_id = action_id

    async def post(
        self, *, message: ChannelMessage, target: JsonValue, new_thread: bool = False
    ) -> PostedChannelMessage:
        text = require_text_content(message)
        route = _trigger_route(target)
        address = dict(route["address"])
        if new_thread:
            address.pop("thread_id", None)
        result = await self._client.post_action(
            route["channel_id"],
            {
                "type": "post",
                "action_id": self._action_id if self._action_id is not None else str(uuid.uuid4()),
                "address": address,
                **({"source_thread_key": route["source_thread_key"]} if not new_thread else {}),
                "message": {"text": text},
            },
        )
        return {"id": result["id"]}


async def start_trigger_thread(
    client: TriggerClientLike, *, target: object, text: str, action_id: str
) -> TriggerStartedThread | None:
    """Post the first message of a new thread in the target's conversation.

    Trigger follows the thread. Returns it, unless Trigger cannot name it.
    """
    route = _trigger_route(target)
    address = dict(route["address"])
    address.pop("thread_id", None)
    result = await client.post_action(
        route["channel_id"],
        {
            "type": "post",
            "action_id": action_id,
            "address": address,
            "follow_thread": True,
            "message": {"text": text},
        },
    )
    return result.get("thread")


class _TriggerRoute(TypedDict):
    channel_id: str
    address: Mapping[str, object]
    source_thread_key: str


def _trigger_route(target: object) -> _TriggerRoute:
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
    return {"channel_id": channel_id, "address": address, "source_thread_key": source_thread_key}
