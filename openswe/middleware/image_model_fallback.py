"""Keep retained images away from text-only models."""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel

from openswe.middleware.trace import OpenSWEMiddleware


class ImageModelFallbackMiddleware(OpenSWEMiddleware):
    def __init__(self, fallback: BaseChatModel) -> None:
        self._text_only_models: list[BaseChatModel] = []
        self._fallback = fallback

    def add_text_only_model(self, model: BaseChatModel) -> None:
        self._text_only_models.append(model)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if any(request.model is model for model in self._text_only_models) and any(
            block.get("type") == "image"
            for message in request.messages
            for block in message.content_blocks
        ):
            request = request.override(model=self._fallback)
        return await handler(request)
