"""Which surface a turn owes its answer to, and what asks the model again for it.

A model that narrates its answer instead of calling the reply tool leaves the
asker with nothing while the run is still recorded a success; MDA's loop nudges it
(``ReplyRules``). Which surface owes an answer is carried in `reply_surface`, resolved
per run and flipped mid-run when the conversation moves between Slack and the web.
"""

import logging
from collections.abc import Mapping
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage

from openswe.input_messages import (
    SystemIdentity,
    build_input_messages,
    dynamic_context_hash,
    message_sender_id,
)
from openswe.middleware.message_content import content_to_text
from openswe.prompts import prompt

logger = logging.getLogger(__name__)

# Tools that post a card as the turn's final Slack reply.
CARD_TOOLS = frozenset({"connect_managed_tools"})

ReplySurface = Literal["slack", "web"]

SLACK_REPLY_SURFACE: ReplySurface = "slack"
WEB_REPLY_SURFACE: ReplySurface = "web"

REPLY_GUARD: SystemIdentity = {
    "id": "system:reply-guard",
    "display_name": "Reply guard",
    "platform": "open-swe",
}

WORK_STATE_GUARD: SystemIdentity = {
    "id": "system:work-state-guard",
    "display_name": "Work state guard",
    "platform": "open-swe",
}


def current_reply_surface(state: Mapping[str, Any]) -> ReplySurface:
    """The surface this thread currently owes its answer to."""
    return (
        SLACK_REPLY_SURFACE
        if state.get("reply_surface") == SLACK_REPLY_SURFACE
        else WEB_REPLY_SURFACE
    )


def _starts_turn(message: BaseMessage) -> bool:
    if not isinstance(message, HumanMessage):
        return False
    if dynamic_context_hash(message.content) is not None:
        return False
    return message_sender_id(message.content, kind="system") != REPLY_GUARD["id"]


def reply_nudge(
    tool_name: str, no_reply_tool_name: str, introduced: set[str] | None = None
) -> list[str]:
    """What asks the model again for the reply it owes, introducing the guard unless ``introduced``."""
    instruction = prompt(
        "runs/missing-user-reply",
        {"reply_tool": tool_name, "no_reply_tool": no_reply_tool_name},
    )
    built = build_input_messages(
        instruction,
        {"sender_id": REPLY_GUARD["id"], "surface": "automation", "kind": "system"},
        systems=[REPLY_GUARD],
        injected_dynamic_context_hashes=set(introduced or ()),
    )
    return [content_to_text(message["content"]) for message in built]


def work_state_nudge(tool_name: str) -> list[str]:
    """What sends a goal-mode run back to work until it reports where the work stands."""
    built = build_input_messages(
        prompt("runs/unfinished-work", tool=tool_name),
        {"sender_id": WORK_STATE_GUARD["id"], "surface": "automation", "kind": "system"},
        systems=[WORK_STATE_GUARD],
    )
    return [content_to_text(message["content"]) for message in built]
