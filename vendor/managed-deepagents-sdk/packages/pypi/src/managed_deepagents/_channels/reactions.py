"""Resolve the emoji the agent reacts with when it picks up an inbound message.

The reaction is a courtesy and must never fail a run or delay a reply, so a
chooser that raises, hangs, or answers with something that is not an emoji
short name yields :data:`DEFAULT_REACTION_EMOJI` instead. Returning ``None``
skips the reaction.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable
from typing import TypedDict

from managed_deepagents._log import logger

#: The default reaction, and the fallback whenever a chooser does not answer.
DEFAULT_REACTION_EMOJI = "eyes"

#: Slack emoji short name, without the surrounding colons.
#:
#: Well-formedness is all that can be checked here; whether Slack knows the name
#: is Slack's to say, and it rejects an unknown one by doing nothing.
_EMOJI_NAME = re.compile(r"^[a-z0-9+-][a-z0-9_+-]{0,59}$")

#: How long a chooser may take before the default is used instead.
_CHOOSER_TIMEOUT_S = 5.0


class ReactionContext(TypedDict):
    """What a chooser is told about the message it is reacting to."""

    #: The inbound message text. Attacker-controlled; treat it as data.
    text: str


#: Picks the emoji for one inbound message, or ``None`` for none. May be sync or async.
#:
#: A sync chooser runs in a thread, where the timeout cannot stop it, so bound
#: any I/O it does.
ReactionChooser = Callable[[ReactionContext], str | Awaitable[str | None] | None]

#: How a channel reacts: ``True`` with the default, ``False`` not at all, a short
#: name always that emoji, or a callable per message.
ReactionChoice = bool | str | ReactionChooser


def is_emoji_name(value: object) -> bool:
    """Whether ``value`` is a usable emoji short name."""
    # `fullmatch`, not `match`: Python's `$` also matches before a trailing
    # newline, so `match` would accept "bug\n" and Slack would decline it.
    return isinstance(value, str) and _EMOJI_NAME.fullmatch(value) is not None


async def _call(choice: ReactionChooser, context: ReactionContext) -> object:
    """Call ``choice``, giving a sync one a thread so it cannot block the loop.

    A plain ``def`` may still return an awaitable, so await that too.
    """
    if inspect.iscoroutinefunction(choice):
        return await choice(context)
    result = await asyncio.to_thread(choice, context)
    return await result if inspect.isawaitable(result) else result


async def resolve_reaction_emoji(choice: ReactionChoice, context: ReactionContext) -> str | None:
    """Resolve the reaction for one message. Never raises."""
    if isinstance(choice, str):
        return choice if is_emoji_name(choice) else DEFAULT_REACTION_EMOJI
    if not callable(choice):
        return DEFAULT_REACTION_EMOJI

    try:
        chosen = await asyncio.wait_for(_call(choice, context), timeout=_CHOOSER_TIMEOUT_S)
        if chosen is None:
            return None
        if is_emoji_name(chosen):
            return str(chosen)
        logger.warning(
            "Channel reaction chooser did not answer with an emoji short name; using the default."
        )
    except Exception as error:  # noqa: BLE001 - a courtesy must not fail a run
        logger.warning(
            "Channel reaction chooser failed (%s); using the default.",
            type(error).__name__,
        )
    return DEFAULT_REACTION_EMOJI


__all__ = [
    "DEFAULT_REACTION_EMOJI",
    "ReactionChoice",
    "ReactionChooser",
    "ReactionContext",
    "is_emoji_name",
    "resolve_reaction_emoji",
]
