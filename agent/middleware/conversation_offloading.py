"""Expose Deep Agents compaction without streaming its internal model output."""

import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict, cast

from deepagents.middleware.summarization import (
    DEEPAGENTS_DEFAULT_SUMMARY_PROMPT,
    ContextSize,
    SummarizationMiddleware,
    SummarizationState,
    compute_summarization_defaults,
)
from langchain.agents.middleware.summarization import (
    SummarizationMiddleware as LangChainSummarizationMiddleware,
)
from langchain.agents.middleware.types import (
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    hook_config,
)
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langgraph.config import get_stream_writer
from langgraph.runtime import Runtime

from agent.analytics.usage import record_context_offloaded

logger = logging.getLogger(__name__)

_manual = ContextVar("manual_offloading", default=False)
_overflow_cause = ContextVar("offloading_overflow_cause", default=False)

OffloadTrigger = Literal["manual", "automatic", "overflow"]


class _CodingSummarizationMiddleware(LangChainSummarizationMiddleware):
    """Summarization that keeps the trailing tool exchange when trimming.

    The upstream helper trims the slice to `trim_tokens_to_summarize` tokens
    with `start_on="human"`. A coding trajectory has one human turn at the
    start and then nothing but ai/tool messages, so the trailing window it
    keeps contains no human at all and `trim_messages` returns an empty list.
    `_acreate_summary` then reports "Previous conversation was too long to
    summarize." instead of a summary, and every compaction silently discards
    the entire history.

    Starting the kept window on an ai/tool message is what this trajectory
    needs: the first retained message must only not be an orphaned tool result,
    because a tool message without its call is not a valid conversation.
    """

    def _trim_messages_for_summary(self, messages: list[AnyMessage]) -> list[AnyMessage]:
        if self.trim_tokens_to_summarize is None:
            return messages
        trimmed = cast(
            "list[AnyMessage]",
            trim_messages(
                messages,
                max_tokens=self.trim_tokens_to_summarize,
                token_counter=self.token_counter,
                start_on=("human", "ai"),
                strategy="last",
                allow_partial=True,
                include_system=True,
            ),
        )
        if trimmed or not messages:
            return trimmed
        # `allow_partial` can still leave only a tool message when the tail is a
        # single oversized result; drop it so the model sees no orphaned call.
        trailing: list[AnyMessage] = [
            message for message in messages if not isinstance(message, ToolMessage)
        ]
        return trailing[-1:] if trailing else messages[-1:]


@dataclass(frozen=True)
class CompactionSettings:
    """Per-run compaction strategy overrides used by offline experiments.

    ``None`` keeps the model-derived default for that dimension. The summary
    prompt is the complete template expected by the summarization middleware,
    including its ``{messages}`` placeholder.
    """

    trigger_tokens: int | None = None
    trigger_messages: int | None = None
    trigger_fraction: float | None = None
    summary_prompt: str | None = None

    def trigger(self, default: ContextSize) -> ContextSize:
        if self.trigger_tokens is not None:
            return ("tokens", self.trigger_tokens)
        if self.trigger_messages is not None:
            return ("messages", self.trigger_messages)
        if self.trigger_fraction is not None:
            return ("fraction", self.trigger_fraction)
        return default


class OffloadMetrics(TypedDict, total=False):
    """Token and duration estimates for one compaction.

    Token counts come from the middleware token counter and cover the messages
    replaced by the summary (``summarized_tokens``) and the summary itself
    (``summary_tokens``). ``summary_tokens`` is absent when summarization failed.
    """

    messages_summarized: int
    summarized_tokens: int
    summary_tokens: int
    duration_ms: int


class OffloadTotals(TypedDict):
    """Run-level compaction totals persisted in graph state.

    Evaluations read these totals from state because compaction model calls do
    not appear in the message list.
    """

    count: int
    total_messages_summarized: int
    total_summarized_tokens: int
    total_summary_tokens: int
    total_duration_ms: int
    overflow_count: int


class OffloadState(TypedDict, total=False):
    """State value describing compaction; last-event details plus run totals."""

    status: Literal["started", "completed", "failed", "skipped"]
    trigger: OffloadTrigger
    cutoff_index: int | None
    file_path: str | None
    messages_summarized: int
    summarized_tokens: int
    summary_tokens: int
    duration_ms: int
    count: int
    total_messages_summarized: int
    total_summarized_tokens: int
    total_summary_tokens: int
    total_duration_ms: int
    overflow_count: int


_offload_metrics: ContextVar[OffloadMetrics | None] = ContextVar("offload_metrics", default=None)

_TOTAL_KEYS = (
    "count",
    "total_messages_summarized",
    "total_summarized_tokens",
    "total_summary_tokens",
    "total_duration_ms",
    "overflow_count",
)


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _count_value(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _prior_totals(state: object) -> OffloadTotals:
    prior = state if isinstance(state, dict) else {}
    return {
        "count": _count_value(prior.get("count")),
        "total_messages_summarized": _count_value(prior.get("total_messages_summarized")),
        "total_summarized_tokens": _count_value(prior.get("total_summarized_tokens")),
        "total_summary_tokens": _count_value(prior.get("total_summary_tokens")),
        "total_duration_ms": _count_value(prior.get("total_duration_ms")),
        "overflow_count": _count_value(prior.get("overflow_count")),
    }


class OffloadingState(SummarizationState):
    conversation_offloading: NotRequired[OffloadState]


class ConversationOffloadingMiddleware(SummarizationMiddleware):
    state_schema = OffloadingState

    @property
    def name(self) -> str:
        return "SummarizationMiddleware"

    def __init__(
        self,
        model: Any,
        backend: Any,
        *,
        manual: bool = False,
        settings: CompactionSettings | None = None,
    ) -> None:
        summary_model = model.model_copy(
            update={"tags": [*(model.tags or []), "nostream", "langsmith:hidden"]}
        )
        defaults = compute_summarization_defaults(model)
        trigger = (
            settings.trigger(defaults["trigger"]) if settings is not None else defaults["trigger"]
        )
        prompt = (
            settings.summary_prompt
            if settings is not None and settings.summary_prompt is not None
            else DEEPAGENTS_DEFAULT_SUMMARY_PROMPT
        )
        super().__init__(
            model=summary_model,
            backend=backend,
            trigger=trigger,
            keep=defaults["keep"],
            truncate_args_settings=defaults["truncate_args_settings"],
            summary_prompt=prompt,
        )
        # Deep Agents builds its own summarization helper in `__init__` and
        # delegates the summary steps to it, so replacing the helper is the only
        # way to change trimming without forking the rest of that class. The
        # remaining arguments mirror the ones the base constructor passes.
        self._lc_helper = _CodingSummarizationMiddleware(
            model=summary_model,
            trigger=trigger,
            keep=defaults["keep"],
            token_counter=count_tokens_approximately,
            summary_prompt=prompt,
        )
        self.manual = manual
        # Keyed by summary text: `awrap_model_call` generates summaries inside
        # `asyncio.gather`, so task-local context does not carry metrics back.
        self._pending_metrics: dict[str, OffloadMetrics] = {}

    def _trigger(self) -> OffloadTrigger:
        if _manual.get():
            return "manual"
        return "overflow" if _overflow_cause.get() else "automatic"

    def _status(
        self,
        status: Literal["started", "completed", "failed", "skipped"],
        **details: Any,
    ) -> OffloadState:
        payload: OffloadState = {
            "status": status,
            "trigger": self._trigger(),
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
        started = time.perf_counter()
        metrics: OffloadMetrics = {"messages_summarized": len(messages_to_summarize)}
        try:
            metrics["summarized_tokens"] = self._count_tokens(messages_to_summarize, None, None)
        except Exception:
            logger.warning("Failed to count summarized tokens", exc_info=True)
        try:
            summary = await super()._acreate_summary(messages_to_summarize)
        except Exception:
            metrics["duration_ms"] = _elapsed_ms(started)
            self._status("failed")
            await record_context_offloaded(
                trigger=self._trigger(),
                status="failed",
                **metrics,
            )
            raise
        try:
            metrics["summary_tokens"] = self._count_tokens([AIMessage(content=summary)], None, None)
        except Exception:
            logger.warning("Failed to count summary tokens", exc_info=True)
        metrics["duration_ms"] = _elapsed_ms(started)
        self._pending_metrics[summary] = metrics
        return summary

    def _build_new_messages_with_path(
        self, summary: str, file_path: str | None
    ) -> list[AnyMessage]:
        messages = super()._build_new_messages_with_path(summary, file_path)
        metrics = self._pending_metrics.pop(summary, {})
        _offload_metrics.set(metrics)
        self._status("completed", file_path=file_path, **metrics)
        return messages

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse:
        _offload_metrics.set(None)
        overflow_token = _overflow_cause.set(False)

        async def detect_overflow(inner: ModelRequest) -> ModelResponse:
            # The upstream middleware retries an over-budget call after
            # summarizing; recording the cause here is what separates an
            # overflow escape from a threshold-triggered compaction.
            try:
                return await handler(inner)
            except ContextOverflowError:
                _overflow_cause.set(True)
                raise

        try:
            response = await super().awrap_model_call(request, detect_overflow)
            metrics: OffloadMetrics = _offload_metrics.get() or {}
            trigger = self._trigger()
        finally:
            _offload_metrics.set(None)
            _overflow_cause.reset(overflow_token)
        if isinstance(response, ExtendedModelResponse) and response.command:
            update = response.command.update
            if isinstance(update, dict) and (event := update.get("_summarization_event")):
                cutoff = event.get("cutoff_index")
                totals = _prior_totals(request.state.get("conversation_offloading"))
                update["conversation_offloading"] = {
                    "status": "completed",
                    "trigger": trigger,
                    "cutoff_index": event["cutoff_index"],
                    "file_path": event["file_path"],
                    **metrics,
                    "count": totals["count"] + 1,
                    "total_messages_summarized": totals["total_messages_summarized"]
                    + metrics.get("messages_summarized", 0),
                    "total_summarized_tokens": totals["total_summarized_tokens"]
                    + metrics.get("summarized_tokens", 0),
                    "total_summary_tokens": totals["total_summary_tokens"]
                    + metrics.get("summary_tokens", 0),
                    "total_duration_ms": totals["total_duration_ms"]
                    + metrics.get("duration_ms", 0),
                    "overflow_count": totals["overflow_count"]
                    + (1 if trigger == "overflow" else 0),
                }
                await record_context_offloaded(
                    trigger=trigger,
                    status="completed",
                    cutoff_index=cutoff if isinstance(cutoff, int) else None,
                    **metrics,
                )
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
                totals = _prior_totals(state.get("conversation_offloading"))
                update = {"conversation_offloading": {**self._status("skipped"), **totals}}
            return {**update, "jump_to": "end"}
        finally:
            _manual.reset(token)
