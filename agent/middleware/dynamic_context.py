from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AnyMessage

from ..input_messages import dynamic_context_hash, dynamic_context_messages


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
            request = request.override(messages=[*restored, *request.messages])
        return await handler(request)
