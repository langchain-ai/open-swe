"""A handler's reply: start nothing, and post a message where the event came from."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

from pydantic import ValidationError

from managed_deepagents._channels.runtime import validate_message

if TYPE_CHECKING:
    from collections.abc import Mapping

    from managed_deepagents._channels.run_input import ChannelRunResult
    from managed_deepagents._channels.types import ChannelMessage


@dataclass(frozen=True)
class ChannelReplyResult:
    """Start nothing, and post ``message`` where the event came from, as
    ``runtime.channel.post`` would. An event handler or ``resolve_user`` returns
    it with :func:`reply`; an endpoint answers ``{"type": "reply", "message"}``.

    Experimental: this may change in a minor release."""

    message: ChannelMessage
    type: Literal["reply"] = field(default="reply", init=False)


def reply(message: ChannelMessage | str) -> ChannelReplyResult:
    """Start nothing, and post ``message`` where the event came from: text, or a
    ``ChannelMessage``. A Linear agent session takes text only.

    .. code-block:: python

        def on_session(event, thread):
            if repository_of(event) is None:
                return reply("Which repository should I work in?")
            return event.payload["promptContext"]

    Experimental: this may change in a minor release.
    """
    checked: ChannelMessage = (
        {"type": "content", "content": message} if isinstance(message, str) else message
    )
    try:
        validate_message(cast("Mapping[str, object]", checked))
    except (ValidationError, TypeError, ValueError):
        raise TypeError("reply takes text or a ChannelMessage") from None
    return ChannelReplyResult(checked)


if TYPE_CHECKING:
    HandlerResult: TypeAlias = ChannelRunResult | ChannelReplyResult
    """What a channel handler returns other than ``None``: run input, or a reply."""


__all__ = ["ChannelReplyResult", "reply"]
