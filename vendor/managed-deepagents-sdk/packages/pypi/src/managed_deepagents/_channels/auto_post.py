"""A channel's ``auto_post``, or its deprecated ``reply``."""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from managed_deepagents._channels.types import CHANNEL_REPLIES

if TYPE_CHECKING:
    from managed_deepagents._channels.types import ChannelReply


def channel_auto_post(
    auto_post: bool | None,
    reply: ChannelReply | None,
    *,
    label: str,
    error: type[Exception] = TypeError,
) -> bool:
    """Whether a channel posts a run's final answer where it came from: ``auto_post``,
    ``True`` by default.

    The deprecated ``reply`` still works: ``"auto"`` is ``True`` and ``"none"``
    is ``False``. Setting both raises. A target isn't taken yet.
    """
    if reply is None:
        if auto_post is None:
            return True
        if not isinstance(auto_post, bool):
            raise error("channel auto_post must be true or false")
        return auto_post
    if auto_post is not None:
        raise error("channel takes auto_post or reply, not both")
    if reply not in CHANNEL_REPLIES:
        raise error(f"{label} reply must be auto or none")
    warnings.warn(
        f'{label} reply is deprecated; use auto_post (reply="none" is auto_post=False).',
        FutureWarning,
        stacklevel=3,
    )
    return reply == "auto"


def reply_of(auto_post: bool) -> ChannelReply:
    """A channel's ``auto_post`` as the ``reply`` its bindings and deploy read."""
    return "auto" if auto_post else "none"
