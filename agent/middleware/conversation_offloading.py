"""Expose Deep Agents compaction without streaming its internal model output."""

from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any, NotRequired
from uuid import UUID

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
    ToolCallRequest,
    hook_config,
)
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langgraph.config import get_config, get_stream_writer
from langgraph.runtime import Runtime
from langgraph.types import Command

_manual = ContextVar("manual_offloading", default=False)

_SUMMARY_OVERFLOW = "Previous conversation was too long to summarize."
_MAX_SUMMARIZATIONS = 10
_MAX_HISTORY_PAGES = 5
_TAIL_MESSAGES = 4
_TAIL_CHARS = 4000
_COMPACTION_LIMIT_MESSAGE = "Turn stopped because conversation compaction exceeded its limit."


@dataclass
class _InvocationState:
    summarizations: int = 0
    history_pages: set[tuple[str, int, int]] = field(default_factory=set)
    degraded_summary: bool = False


_invocations: dict[str, _InvocationState] = {}


def _invocation_key() -> str | None:
    try:
        config = get_config()
    except RuntimeError:
        return None
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        configurable = {}
    thread_id = configurable.get("thread_id")
    run_id = config.get("run_id") or configurable.get("run_id")
    if not isinstance(thread_id, str):
        return None
    if isinstance(run_id, (str, UUID)):
        return f"{thread_id}:{run_id}"
    return thread_id


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

    def _invocation_state(self) -> _InvocationState:
        key = _invocation_key()
        if key is None:
            state = getattr(self, "_local_invocation", None)
            if state is None:
                state = _InvocationState()
                self._local_invocation = state
            return state
        return _invocations.setdefault(key, _InvocationState())

    @staticmethod
    def _history_tail(messages: list[AnyMessage]) -> str:
        tail: list[str] = []
        for message in messages[-_TAIL_MESSAGES:]:
            content = message.content if isinstance(message.content, str) else str(message.content)
            tail.append(f"{message.type}: {content}")
        return "\n\n".join(tail)[-_TAIL_CHARS:]

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        del state, runtime
        if (key := _invocation_key()) is not None:
            _invocations[key] = _InvocationState()
        else:
            self._local_invocation = _InvocationState()
        return None

    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        del state, runtime
        if (key := _invocation_key()) is not None:
            _invocations.pop(key, None)
        else:
            self._local_invocation = None
        return None

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
            summary = await super()._acreate_summary(messages_to_summarize)
            if not summary.strip() or summary.strip() == _SUMMARY_OVERFLOW:
                state = self._invocation_state()
                state.degraded_summary = True
                return self._history_tail(messages_to_summarize) or _SUMMARY_OVERFLOW
            return summary
        except Exception:
            self._status("failed")
            raise

    def _build_new_messages_with_path(
        self, summary: str, file_path: str | None
    ) -> list[AnyMessage]:
        state = self._invocation_state()
        if state.degraded_summary:
            self._status("degraded", file_path=file_path)
            state.degraded_summary = False
        state.summarizations += 1
        messages = super()._build_new_messages_with_path(summary, file_path)
        self._status("completed", file_path=file_path)
        return messages

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_call = request.tool_call
        if tool_call.get("name") == "read_file":
            args = tool_call.get("args")
            if isinstance(args, Mapping):
                file_path = args.get("file_path")
                if isinstance(file_path, str) and fnmatch(
                    file_path, "/conversation_history/session_*.md"
                ):
                    offset = args.get("offset", 0)
                    limit = args.get("limit", 100)
                    if isinstance(offset, int) and isinstance(limit, int):
                        page = (file_path, offset, limit)
                        state = self._invocation_state()
                        if page in state.history_pages:
                            return ToolMessage(
                                content="read_file page already served; use the existing content instead.",
                                tool_call_id=tool_call.get("id"),
                            )
                        if len(state.history_pages) >= _MAX_HISTORY_PAGES:
                            return ToolMessage(
                                content="read_file history page budget exhausted after five unique pages.",
                                tool_call_id=tool_call.get("id"),
                            )
                        state.history_pages.add(page)
        return await handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        if self._invocation_state().summarizations >= _MAX_SUMMARIZATIONS:
            return ModelResponse(result=[AIMessage(content=_COMPACTION_LIMIT_MESSAGE)])
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
