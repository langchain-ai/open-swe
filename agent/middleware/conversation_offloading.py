"""Expose Deep Agents compaction without streaming its internal model output."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, NotRequired

from deepagents.middleware.summarization import (
    SummarizationMiddleware,
    SummarizationState,
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


class OffloadingState(SummarizationState):
    conversation_offloading: NotRequired[dict[str, Any]]


class ConversationOffloadingMiddleware(SummarizationMiddleware):
    state_schema = OffloadingState

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

    def _status(self, status: str, **details: Any) -> dict[str, Any]:
        payload = {
            "status": status,
            "trigger": "manual" if _manual.get() else "automatic",
            **details,
        }
        get_stream_writer()({"type": "conversation_offloading", **payload})
        return payload

    def _should_summarize(self, messages: list[AnyMessage], total_tokens: int) -> bool:
        return _manual.get() or super()._should_summarize(messages, total_tokens)

    def _determine_cutoff_index(self, messages: list[AnyMessage]) -> int:
        cutoff = super()._determine_cutoff_index(messages)
        if not self._filter_summary_messages(messages[:cutoff]):
            return 0
        return cutoff

    async def _acreate_summary(self, messages_to_summarize: list[AnyMessage]) -> str:
        self._status("started")
        try:
            return await super()._acreate_summary(messages_to_summarize)
        except Exception:
            self._status("failed")
            raise

    def _build_new_messages_with_path(
        self, summary: str, file_path: str | None
    ) -> list[AnyMessage]:
        messages = super()._build_new_messages_with_path(summary, file_path)
        self._status("completed", file_path=file_path)
        return messages

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        response = await super().awrap_model_call(request, handler)
        if isinstance(response, ExtendedModelResponse) and response.command:
            update = response.command.update
            if isinstance(update, dict) and (event := update.get("_summarization_event")):
                update["conversation_offloading"] = {
                    "status": "completed",
                    "trigger": "manual" if _manual.get() else "automatic",
                    "cutoff_index": event["cutoff_index"],
                    "file_path": event["file_path"],
                }
        return response

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        if not self.manual:
            return None

        async def finish(request: ModelRequest) -> ModelResponse:
            return ModelResponse(result=[])

        token = _manual.set(True)
        try:
            response = await self.awrap_model_call(
                ModelRequest(
                    model=self.model,
                    messages=state.get("messages", []),
                    tools=[],
                    state=state,
                    runtime=runtime,
                ),
                finish,
            )
            update = (
                response.command.update
                if isinstance(response, ExtendedModelResponse) and response.command
                else None
            )
            if not isinstance(update, dict):
                update = {"conversation_offloading": self._status("skipped")}
            return {**update, "jump_to": "end"}
        finally:
            _manual.reset(token)
