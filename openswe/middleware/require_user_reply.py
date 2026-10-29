"""Keep a turn that owes the user an answer from ending in silence.

A model that narrates its answer instead of calling the reply tool leaves the
asker with nothing while the run is still recorded a success. Which surface owes
an answer is carried in `reply_surface`, resolved per run and flipped mid-run
when the conversation moves between Slack and the web.
"""

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal, NotRequired

from langchain.agents.middleware.types import AgentState, OmitFromOutput, hook_config
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.runtime import Runtime

from openswe.input_messages import (
    SystemIdentity,
    build_input_messages,
    dynamic_context_hash,
    message_sender_id,
    visible_dynamic_context_hashes,
)
from openswe.middleware.message_content import content_to_text
from openswe.middleware.trace import OpenSWEMiddleware
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


class ReplySurfaceState(AgentState):
    reply_surface: NotRequired[Annotated[ReplySurface, OmitFromOutput]]
    reply_nudges: NotRequired[Annotated[int, OmitFromOutput]]


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


def turn_tail(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """Messages produced since the last thing a person said."""
    for index in range(len(messages) - 1, -1, -1):
        if _starts_turn(messages[index]):
            return list(messages[index + 1 :])
    return list(messages)


def reported_failure(message: ToolMessage) -> bool:
    if message.status == "error":
        return True
    try:
        payload = json.loads(content_to_text(message.content))
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("success") is False


def _last_answer(messages: Sequence[BaseMessage]) -> str:
    """The latest non-empty assistant text this turn."""
    for message in reversed(turn_tail(messages)):
        if isinstance(message, AIMessage) and (text := content_to_text(message.content).strip()):
            return text
    return ""


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


class RequireUserReplyMiddleware(OpenSWEMiddleware):
    """Re-invoke the model when a turn that owes the user an answer ends without one.

    The retry budget is small: a model that will not use the tool after two
    nudges will not use it on the tenth either. Past the budget the last
    assistant text is posted on its behalf, because silence is the one outcome
    the asker cannot recover from.
    """

    state_schema = ReplySurfaceState

    def __init__(
        self,
        tool_name: str,
        no_reply_tool_name: str,
        *,
        initial_surface: ReplySurface,
        max_retries: int = 2,
        replies: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._no_reply_tool_name = no_reply_tool_name
        self._initial_surface = initial_surface
        self._max_retries = max_retries
        # Other tools whose successful call answers the person, such as a walkthrough's chunk.
        self._replies = replies

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, object]:  # noqa: ARG002
        # Resolved fresh every run: the surface a previous run ended on says
        # nothing about where this one was triggered from.
        return {"reply_surface": self._initial_surface, "reply_nudges": 0}

    def _discharges_turn(self, call: Mapping[str, Any]) -> bool:
        name = call.get("name")
        if (
            name == self._no_reply_tool_name
            or name in self._replies
            or (self._tool_name == "slack_reply" and name in CARD_TOOLS)
        ):
            return True
        # An acknowledgement is not an answer, and the Slack prompt orders one
        # before any investigation — counting it would leave every turn "replied".
        args = call.get("args")
        return name == self._tool_name and (
            isinstance(args, Mapping) and args.get("response_type") == "final"
        )

    @staticmethod
    def _requires_completion_reply(message: ToolMessage) -> bool:
        try:
            payload = json.loads(content_to_text(message.content))
        except ValueError:
            return False
        return (
            isinstance(payload, dict)
            and payload.get("success") is True
            and payload.get("completion_reply_required") is True
        )

    @staticmethod
    def _successful_call_ids(messages: Sequence[BaseMessage]) -> set[str]:
        return {
            message.tool_call_id
            for message in messages
            if isinstance(message, ToolMessage) and not reported_failure(message)
        }

    def _satisfied(self, messages: Sequence[BaseMessage]) -> bool:
        tail = turn_tail(messages)
        completion_reply_required = any(
            isinstance(message, ToolMessage) and self._requires_completion_reply(message)
            for message in tail
        )
        call_ids = {
            call.get("id")
            for message in tail
            if isinstance(message, AIMessage)
            for call in message.tool_calls
            if self._discharges_turn(call)
            and not (completion_reply_required and call.get("name") == self._no_reply_tool_name)
        }
        return bool(call_ids & self._successful_call_ids(tail))

    async def _post_on_behalf(self, state: Mapping[str, Any]) -> None:
        text = _last_answer(state.get("messages") or [])
        if not text:
            logger.warning("Nothing to post on the model's behalf: it wrote no text this turn")
            return
        from openswe.slack.tools.reply import send_slack_reply

        result = await send_slack_reply(text, "final", state=dict(state))
        logger.warning(
            "Posted the model's final message on its behalf after it ignored the reply tool",
            extra={"reply_tool": self._tool_name, "reply_fallback_result": result},
        )

    def _nudge(self, state: Mapping[str, Any]) -> list[HumanMessage]:
        built = reply_nudge(
            self._tool_name, self._no_reply_tool_name, visible_dynamic_context_hashes(state)
        )
        return [HumanMessage(content=text, id=str(uuid.uuid7())) for text in built]

    @hook_config(can_jump_to=["model", "end"])
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:  # noqa: ARG002
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage) or current_reply_surface(state) != SLACK_REPLY_SURFACE:
            return None
        suppressed: list[AIMessage] = []
        update: dict[str, object] = {}
        if last.tool_calls:
            if not all(call["name"] == self._no_reply_tool_name for call in last.tool_calls):
                return None
            tail = turn_tail(messages[:-1])
            no_reply_ids = {
                call["id"]
                for message in tail
                if isinstance(message, AIMessage)
                for call in message.tool_calls
                if call["name"] == self._no_reply_tool_name
            }
            if not no_reply_ids & self._successful_call_ids(tail):
                return None
            suppressed = [
                last.model_copy(
                    update={
                        "tool_calls": [],
                        "additional_kwargs": {
                            key: value
                            for key, value in last.additional_kwargs.items()
                            if key != "tool_calls"
                        },
                    }
                )
            ]
            update = {
                "messages": suppressed,
                "jump_to": "end",
            }
        if self._satisfied(messages):
            return {**update, "reply_nudges": 0}
        nudges = state.get("reply_nudges") or 0
        if nudges >= self._max_retries:
            await self._post_on_behalf(state)
            return {**update, "reply_nudges": 0}
        logger.info(
            "Re-invoking the model for a missing user-facing reply",
            extra={"reply_tool": self._tool_name, "reply_attempt": nudges + 1},
        )
        return {
            "reply_nudges": nudges + 1,
            "messages": [*suppressed, *self._nudge(state)],
            "jump_to": "model",
        }
