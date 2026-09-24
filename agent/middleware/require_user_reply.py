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

from agent.input_messages import (
    SystemIdentity,
    build_input_messages,
    dynamic_context_hash,
    message_sender_id,
    visible_dynamic_context_hashes,
)
from agent.middleware.message_content import content_to_text
from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import render_prompt

logger = logging.getLogger(__name__)

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


def _turn_tail(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """Messages produced since the last thing a person said."""
    for index in range(len(messages) - 1, -1, -1):
        if _starts_turn(messages[index]):
            return list(messages[index + 1 :])
    return list(messages)


def _reported_failure(message: ToolMessage) -> bool:
    if message.status == "error":
        return True
    try:
        payload = json.loads(content_to_text(message.content))
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("success") is False


def _last_answer(messages: Sequence[BaseMessage]) -> str:
    """The latest non-empty assistant text this turn."""
    for message in reversed(_turn_tail(messages)):
        if isinstance(message, AIMessage) and (text := content_to_text(message.content).strip()):
            return text
    return ""


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
    ) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._no_reply_tool_name = no_reply_tool_name
        self._initial_surface = initial_surface
        self._max_retries = max_retries

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ARG002
        # Resolved fresh every run: the surface a previous run ended on says
        # nothing about where this one was triggered from.
        return {"reply_surface": self._initial_surface, "reply_nudges": 0}

    def _discharges_turn(self, call: Mapping[str, Any]) -> bool:
        name = call.get("name")
        if name == self._no_reply_tool_name:
            return True
        # An acknowledgement is not an answer, and the Slack prompt orders one
        # before any investigation — counting it would leave every turn "replied".
        args = call.get("args")
        return name == self._tool_name and (
            isinstance(args, Mapping) and args.get("response_type") == "final"
        )

    def _satisfied(self, messages: Sequence[BaseMessage]) -> bool:
        tail = _turn_tail(messages)
        call_ids = {
            call.get("id")
            for message in tail
            if isinstance(message, AIMessage)
            for call in message.tool_calls
            if self._discharges_turn(call)
        }
        if not call_ids:
            return False
        return any(
            isinstance(message, ToolMessage)
            and message.tool_call_id in call_ids
            and not _reported_failure(message)
            for message in tail
        )

    async def _post_on_behalf(self, state: Mapping[str, Any]) -> None:
        text = _last_answer(state.get("messages") or [])
        if not text:
            logger.warning("Nothing to post on the model's behalf: it wrote no text this turn")
            return
        from agent.slack.tools.reply import slack_reply

        result = await slack_reply(text, "final", state=dict(state))
        logger.warning(
            "Posted the model's final message on its behalf after it ignored the reply tool",
            extra={"reply_tool": self._tool_name, "reply_fallback_result": result},
        )

    def _nudge(self, state: Mapping[str, Any]) -> list[HumanMessage]:
        instruction = render_prompt(
            "runs/missing-user-reply.md",
            {"reply_tool": self._tool_name, "no_reply_tool": self._no_reply_tool_name},
        )
        built = build_input_messages(
            instruction,
            {"sender_id": REPLY_GUARD["id"], "surface": "automation", "kind": "system"},
            systems=[REPLY_GUARD],
            injected_dynamic_context_hashes=visible_dynamic_context_hashes(state),
        )
        return [
            HumanMessage(content=content_to_text(m["content"]), id=str(uuid.uuid7())) for m in built
        ]

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:  # noqa: ARG002
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None
        if current_reply_surface(state) != SLACK_REPLY_SURFACE:
            return None
        if self._satisfied(messages):
            return {"reply_nudges": 0}
        nudges = state.get("reply_nudges") or 0
        if nudges >= self._max_retries:
            await self._post_on_behalf(state)
            return {"reply_nudges": 0}
        logger.info(
            "Re-invoking the model for a missing user-facing reply",
            extra={"reply_tool": self._tool_name, "reply_attempt": nudges + 1},
        )
        return {"reply_nudges": nudges + 1, "messages": self._nudge(state), "jump_to": "model"}
