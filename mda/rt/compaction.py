"""Prepare a selected model's final request without replaying history offloads on retry."""

from collections.abc import Awaitable, Callable
from typing import cast

from deepagents.backends import StateBackend
from deepagents.backends.protocol import BackendProtocol
from deepagents.graph import create_summarization_middleware
from deepagents.middleware.summarization import (
    DEEPAGENTS_DEFAULT_SUMMARY_PROMPT,
    SummarizationState,
)
from langchain.agents.middleware import AgentMiddleware, TracePolicy, omit_payload
from langchain.agents.middleware.internal_call_transformer import internal_call_metadata
from langchain.agents.middleware.types import (
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AnyMessage, get_buffer_string
from langgraph.graph.message import add_messages
from langgraph.types import Command
from pydantic import TypeAdapter

from rt.model_budget import ModelCallAttempt

_update = TypeAdapter(dict[str, object])


class SummarizationStateMiddleware(AgentMiddleware):
    """Keep the harness's summary state schema, with compaction deferred to the final request."""

    state_schema = SummarizationState
    trace_policy = TracePolicy(process_inputs=omit_payload)

    @property
    def name(self) -> str:
        return "SummarizationMiddleware"


class PreparedConversation:
    """Accumulate compaction commands locally, including across a smaller-window fallback."""

    def __init__(self, request: ModelRequest, attempt: ModelCallAttempt) -> None:
        self._request = request
        self._attempt = attempt
        self._state = dict(request.state)
        self._state["messages"] = list(request.messages)
        self._updates: dict[str, object] = {}

    async def prepare(
        self, request: ModelRequest, rejected: Exception | None = None
    ) -> ModelRequest:
        backend = getattr(request.runtime, "backend", None)
        backend = backend if isinstance(backend, BackendProtocol) else StateBackend()

        async def summarize(messages: list[AnyMessage]) -> str:
            prompt = DEEPAGENTS_DEFAULT_SUMMARY_PROMPT.format(
                messages=get_buffer_string(messages, format="xml")
            )

            async def invoke() -> ModelResponse:
                response = await request.model.ainvoke(
                    prompt,
                    stream=False,
                    config={"metadata": {"lc_source": "summarization", **internal_call_metadata()}},
                )
                return ModelResponse(result=[response])

            response = await self._attempt.call(invoke, streaming=False)
            return response.result[0].text.strip()

        compact = create_summarization_middleware(request.model, backend, summarizer=summarize)
        selected: ModelRequest | None = None

        async def capture(prepared: ModelRequest) -> ModelResponse:
            nonlocal selected, rejected
            if rejected is not None:
                error, rejected = rejected, None
                raise error
            selected = prepared
            return ModelResponse(result=[])

        state = cast(AgentState, self._state)
        messages = self._state.get("messages", self._request.messages)
        messages = cast(list[AnyMessage], messages)
        response = await compact.awrap_model_call(
            request.override(state=state, messages=messages), capture
        )
        if isinstance(response, ExtendedModelResponse) and response.command is not None:
            update = _update.validate_python(response.command.update)
            replacements = update.pop("messages", None)
            if isinstance(replacements, list):
                merged = add_messages([*messages], replacements)
                self._state["messages"] = merged
                prior = self._updates.get("messages", [])
                self._updates["messages"] = add_messages(
                    [*cast(list[AnyMessage], prior)], replacements
                )
            self._state.update(update)
            self._updates.update(update)
        if selected is None:
            raise RuntimeError("Compaction did not prepare a model request")
        return selected

    async def generate(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        async def invoke() -> ModelResponse:
            return await handler(request)

        return await self._attempt.call(invoke)

    def finish(self, response: ModelResponse) -> ModelResponse | ExtendedModelResponse:
        if not self._updates:
            return response
        return ExtendedModelResponse(model_response=response, command=Command(update=self._updates))


def context_overflow(error: Exception) -> bool:
    if isinstance(error, ContextOverflowError):
        return True
    if getattr(error, "status_code", None) not in {400, 413, 422}:
        return False
    return any(
        marker in str(error).lower()
        for marker in (
            "context_length_exceeded",
            "maximum context length",
            "context window exceeded",
            "exceeds the context window",
            "context limit exceeded",
            "prompt is too long",
        )
    )
