from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AnyMessage, HumanMessage

from ..input_messages import dynamic_context_hash, dynamic_context_messages


def _trailing_request_start(messages: list[AnyMessage]) -> int:
    """Index of the trailing human turn, or the end of the list when there is none.

    Restored context goes here rather than at either edge. The head would shift
    every byte after it and cost the whole cached prefix; the very end would make
    a metadata envelope the model's most recent input instead of the task. Slotting
    it in front of the trailing human turn leaves the cached prefix intact and
    still ends the request on what the user actually asked for. A trailing tool
    result is left alone — nothing may come between it and its tool call.
    """
    cut = len(messages)
    while cut > 0 and isinstance(messages[cut - 1], HumanMessage):
        cut -= 1
    return cut


class DynamicContextMiddleware(AgentMiddleware):
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        effective_hashes = {
            context_hash
            for message in request.messages
            if (context_hash := dynamic_context_hash(message.content)) is not None
        }
        restored: list[AnyMessage] = []
        for message in dynamic_context_messages(request.state.get("messages")):
            context_hash = dynamic_context_hash(message.content)
            if context_hash is None or context_hash in effective_hashes:
                continue
            effective_hashes.add(context_hash)
            restored.append(message)
        if restored:
            cut = _trailing_request_start(request.messages)
            request = request.override(
                messages=[*request.messages[:cut], *restored, *request.messages[cut:]]
            )
        return await handler(request)
