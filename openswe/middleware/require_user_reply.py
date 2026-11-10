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
from runtime_api.catalog import Nudge
from runtime_api.provenance import Provenance

from openswe.input_messages import (
    SystemIdentity,
    build_input_messages,
)
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
    provenance = Provenance.of(message)
    if provenance is not None and provenance.kind == "context":
        return False
    return provenance is None or provenance.sender != REPLY_GUARD["id"]


def reply_nudge(
    tool_name: str, no_reply_tool_name: str, introduced: set[str] | None = None
) -> list[Nudge]:
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
    return [Nudge.of(message) for message in built]
