"""Mirror the run's conversation into the Slack firehose channel."""

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, AnyMessage
from langgraph.runtime import Runtime

from ..input_messages import dynamic_context_hash, input_message_kind, input_message_text
from ..utils.slack_firehose import record_inbound, record_run_end, record_turn

# Synthetic calls the middleware stack injects to keep a run alive; they say
# nothing about what the agent is doing.
_HIDDEN_TOOLS = frozenset({"no_op", "confirming_completion"})


def _message_text(content: Any) -> str:
    text = input_message_text(content)
    if isinstance(text, str):
        return text
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _authored_turns(messages: list[AnyMessage]) -> list[AnyMessage]:
    """The messages a person actually sent.

    A run's human channel also carries the scaffolding Open SWE writes for the
    model — replayed thread context, the block framing where a turn came from,
    per-sender metadata, injected dynamic context. None of that is something
    anyone said, so none of it belongs in the firehose.
    """
    turns: list[AnyMessage] = []
    for message in messages:
        if message.type != "human":
            continue
        if dynamic_context_hash(message.content) is not None:
            continue
        kind = input_message_kind(message.content)
        if kind is not None and kind != "human":
            continue
        turns.append(message)
    return turns


class FirehoseMiddleware(AgentMiddleware):
    """Duplicate every thread into the team's firehose channel, read-only."""

    def __init__(
        self,
        *,
        thread_id: str,
        source: str,
        repo: str | None = None,
        requester: str | None = None,
    ) -> None:
        super().__init__()
        self._thread_id = thread_id
        self._source = source
        self._repo = repo
        self._requester = requester
        self._mirrored_human_ids: set[str] = set()

    def _mirror(self, message: AnyMessage) -> None:
        message_id = getattr(message, "id", None)
        if isinstance(message_id, str):
            if message_id in self._mirrored_human_ids:
                return
            self._mirrored_human_ids.add(message_id)
        record_inbound(
            self._thread_id,
            text=_message_text(message.content),
            message_id=message_id,
            source=self._source,
            repo=self._repo,
            requester=self._requester,
        )

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> None:  # noqa: ARG002
        turns = _authored_turns(state["messages"])
        if not turns:
            return
        # Only the turn that started this run is news; the rest of the thread was
        # mirrored when it arrived.
        for message in turns[:-1]:
            message_id = getattr(message, "id", None)
            if isinstance(message_id, str):
                self._mirrored_human_ids.add(message_id)
        self._mirror(turns[-1])

    async def aafter_model(self, state: AgentState, runtime: Runtime) -> None:  # noqa: ARG002
        # Messages that arrive mid-run are queued into state between model calls.
        for message in _authored_turns(state["messages"]):
            self._mirror(message)
        message = state["messages"][-1]
        if not isinstance(message, AIMessage):
            return
        record_turn(
            self._thread_id,
            text=message.text,
            tool_calls=[
                call for call in message.tool_calls if call.get("name") not in _HIDDEN_TOOLS
            ],
            message_id=message.id,
        )

    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> None:  # noqa: ARG002
        record_run_end(self._thread_id)
