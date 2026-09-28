"""Keep retained images away from a text-only primary model."""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel

from agent.middleware.trace import OpenSWEMiddleware


class ImageModelFallbackMiddleware(OpenSWEMiddleware):
    def __init__(self, primary: BaseChatModel, fallback: BaseChatModel) -> None:
        self._primary = primary
        self._fallback = fallback

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if request.model is self._primary and any(
            block.get("type") == "image"
            for message in request.messages
            for block in message.content_blocks
        ):
            request = request.override(model=self._fallback)
        return await handler(request)
