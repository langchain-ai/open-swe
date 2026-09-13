"""Expose Deep Agents compaction without streaming its internal model output."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from deepagents.middleware.summarization import (
    SummarizationMiddleware,
    compute_summarization_defaults,
)
from langchain.agents.middleware.types import (
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    hook_config,
)
from langchain_core.messages import AnyMessage
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime

_manual = ContextVar("manual_offloading", default=False)


class ConversationOffloadingMiddleware(SummarizationMiddleware):
    @property
    def name(self) -> str:
        return "SummarizationMiddleware"

    def __init__(self, model: Any, backend: Any, *, manual: bool = False) -> None:
        summary_model = model.model_copy(
            update={"tags": [*(model.tags or []), "nostream", "langsmith:hidden"]}
        )
        super().__init__(
            model=summary_model, backend=backend, **compute_summarization_defaults(model)
        )
        self.manual = manual

    def _emit(self, status: str, **details: Any) -> None:
        get_stream_writer()(
            {
                "type": "conversation_offloading",
                "status": status,
                "trigger": "manual" if _manual.get() else "automatic",
                **details,
            }
        )

    def _should_summarize(self, messages: list[AnyMessage], total_tokens: int) -> bool:
        return _manual.get() or super()._should_summarize(messages, total_tokens)

    def _determine_cutoff_index(self, messages: list[AnyMessage]) -> int:
        cutoff = super()._determine_cutoff_index(messages)
        if not self._filter_summary_messages(messages[:cutoff]):
            return 0
        return cutoff

    async def _acreate_summary(self, messages_to_summarize: list[AnyMessage]) -> str:
        self._emit("started")
        try:
            return await super()._acreate_summary(messages_to_summarize)
        except Exception:
            self._emit("failed")
            raise

    def _build_new_messages_with_path(
        self, summary: str, file_path: str | None
    ) -> list[AnyMessage]:
        messages = super()._build_new_messages_with_path(summary, file_path)
        self._emit("completed", file_path=file_path)
        return messages

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        response = await super().awrap_model_call(request, handler)
        if isinstance(response, ExtendedModelResponse) and response.command:
            update = response.command.update
            if isinstance(update, dict) and update.get("_summarization_event"):
                self._emit("completed")
        return response

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        if not self.manual:
            return None

        async def finish(request: ModelRequest) -> ModelResponse:
            return ModelResponse(result=[])

        token = _manual.set(True)
        try:
            await self.awrap_model_call(
                ModelRequest(
                    model=self.model,
                    messages=state.get("messages", []),
                    tools=[],
                    state=state,
                    runtime=runtime,
                ),
                finish,
            )
            return {"jump_to": "end"}
        finally:
            _manual.reset(token)
