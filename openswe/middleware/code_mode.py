"""Code mode: tools the model reaches by writing code, most of them only that way."""

from collections.abc import Awaitable, Callable, Sequence

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware


class CodeModeMiddleware(CodeInterpreterMiddleware):
    """Offer tools inside ``eval``, and take the code-only ones off the model's list.

    Every schema the model sees costs each call its context. Tools the model rarely
    needs are just as reachable as ``tools.<camelCaseName>(...)`` from code, described
    in a few lines of the system prompt instead.
    """

    def __init__(self, ptc: Sequence[str | BaseTool], code_only: frozenset[str]) -> None:
        super().__init__(ptc=list(ptc), subagents=False, mode="turn")
        self._code_only = code_only

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        # The interpreter binds named tools from the request, so they leave it only after.
        async def without_code_only(bound: ModelRequest) -> ModelResponse:
            offered = [t for t in bound.tools if getattr(t, "name", None) not in self._code_only]
            return await handler(bound.override(tools=offered))

        return await super().awrap_model_call(request, without_code_only)
