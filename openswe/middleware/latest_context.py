"""Show the model only the latest context block for each entity."""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import ModelRequest, ModelResponse

from openswe.input_messages import latest_context_only
from openswe.middleware.trace import OpenSWEMiddleware


class LatestContextMiddleware(OpenSWEMiddleware):
    """Drop context blocks a newer block for the same entity replaces; state keeps every version."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        messages = latest_context_only(request.messages)
        if len(messages) != len(request.messages):
            request = request.override(messages=messages)
        return await handler(request)
