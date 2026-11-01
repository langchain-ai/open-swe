"""Fakes for ``SlackConversation`` methods, written against the channel id and the call's arguments."""

import inspect
from collections.abc import Callable

from openswe.slack.client import SlackConversation


def channel_first(fake: Callable[..., object]) -> Callable[..., object]:
    """A ``SlackConversation`` method that calls ``fake`` with the channel id ahead of its arguments."""

    async def method(conversation: SlackConversation, *args: object, **kwargs: object) -> object:
        result = fake(conversation.channel_id, *args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    return method
