from collections.abc import Awaitable, Callable

from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langchain_core.messages.content import ContentBlock

from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import common_prompt


class CommonPromptMiddleware(OpenSWEMiddleware):
    """Include shared service context in every agent's model requests."""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        existing = request.system_message
        content: list[ContentBlock] = [{"type": "text", "text": common_prompt()}]
        if existing is not None:
            content.extend(existing.content_blocks)
        return await handler(request.override(system_message=SystemMessage(content_blocks=content)))


def common_general_purpose_subagent() -> SubAgent:
    """Give isolated default subagents the same service context as their parents."""
    return {**GENERAL_PURPOSE_SUBAGENT, "middleware": [CommonPromptMiddleware()]}
