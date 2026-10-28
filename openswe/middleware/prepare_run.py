import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Annotated, Any, NotRequired, cast

from langchain.agents.middleware.types import (
    AgentState,
    OmitFromOutput,
)
from langgraph.runtime import Runtime

from openswe.middleware.model_call_shape import CallShape
from openswe.middleware.trace import OpenSWEMiddleware
from openswe.utils.startup_trace import aphase, flush_phases
from openswe.utils.thread_reads import shared_thread_reads

logger = logging.getLogger(__name__)


def _take_latest[T](left: T | None, right: T | None) -> T | None:
    return right if right is not None else left


class PrepareRunState(AgentState):
    run_prepared: NotRequired[Annotated[bool, OmitFromOutput, _take_latest]]
    run_prepared_for: NotRequired[Annotated[str, OmitFromOutput, _take_latest]]
    work_dir: NotRequired[Annotated[str | None, OmitFromOutput, _take_latest]]
    rendered_system_prompt: NotRequired[Annotated[str | None, OmitFromOutput, _take_latest]]
    # Set once the prompt is the thread's for good; a thread from before that renders it once more.
    system_prompt_fixed: NotRequired[Annotated[bool, OmitFromOutput, _take_latest]]
    requested_model: NotRequired[Annotated[str | None, OmitFromOutput]]
    requested_effort: NotRequired[Annotated[str | None, OmitFromOutput]]
    selected_model_id: NotRequired[Annotated[str, OmitFromOutput, _take_latest]]
    selected_effort: NotRequired[Annotated[str | None, OmitFromOutput, _take_latest]]


def _latest_message_fingerprint(state: Mapping[str, Any]) -> str | None:
    messages = state.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    latest = messages[-1]
    message_id = getattr(latest, "id", None)
    content = getattr(latest, "content", latest)
    payload = {
        "type": latest.__class__.__name__,
        "id": message_id,
        "content": content,
    }
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RenderedSystemPromptMiddleware(OpenSWEMiddleware):
    """Start each model call's system prompt with the one run preparation rendered."""

    async def ashape_model_call(
        self, call: CallShape, state: Mapping[str, object], runtime: Runtime
    ) -> CallShape:
        rendered = state.get("rendered_system_prompt")
        return call.prepending(rendered) if isinstance(rendered, str) else call


class BasePrepareRunMiddleware(RenderedSystemPromptMiddleware):
    """Checkpointed per-run setup.

    Subclasses must keep `_prepare` idempotent. LangGraph checkpoints the
    `run_prepared_for` latch after this before-agent node, so resumed attempts
    of the same invocation skip completed setup while later invocations on the
    same thread re-prepare fresh tokens and diff context. If a run
    fails before that checkpoint, setup may execute again and every operation it
    calls must tolerate that.
    """

    state_schema = PrepareRunState

    async def abefore_agent(
        self,
        state: AgentState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        if state.get("_deepagents_forked_context"):
            return None
        try:
            prepared_state = cast(PrepareRunState, state)
            fingerprint = self._prepare_fingerprint(prepared_state, runtime)
            if (
                prepared_state.get("run_prepared")
                and prepared_state.get("run_prepared_for") == fingerprint
            ):
                return None
            async with (
                aphase(getattr(self, "_thread_id", None), "prepare.total"),
                shared_thread_reads(),
            ):
                updates = await self._prepare(prepared_state, runtime)
            return {"run_prepared": True, "run_prepared_for": fingerprint, **updates}
        finally:
            # This hook is the first span the startup work can hang off of. A
            # resumed invocation latches out of `_prepare` but its graph factory
            # ran regardless, so the flush has to cover that path too.
            flush_phases(getattr(self, "_thread_id", None))

    def _prepare_fingerprint(self, state: PrepareRunState, runtime: Runtime) -> str:  # noqa: ARG002
        payload = {
            "middleware": self.__class__.__name__,
            "message": _latest_message_fingerprint(state),
            "config": self._prepare_config_fingerprint(),
        }
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _prepare_config_fingerprint(self) -> Any:
        return None

    async def _prepare(self, state: PrepareRunState, runtime: Runtime) -> dict[str, Any]:
        raise NotImplementedError

    async def aresume_run(self, state: AgentState, runtime: Runtime) -> None:
        """Rebuild what `_prepare` kept only in memory, in a process that did not run it.

        A run hosted on Managed Deep Agents calls back into any replica, and the
        checkpointed latch keeps `_prepare` from running again there.
        """
        try:
            await self._rejoin(cast(PrepareRunState, state), runtime)
        except Exception:
            logger.warning(
                "Prepared run rejoin failed",
                extra={"middleware": self.__class__.__name__},
                exc_info=True,
            )

    async def aflush_run(self, runtime: Runtime) -> None:
        del runtime

    async def _rejoin(self, state: PrepareRunState, runtime: Runtime) -> None:
        del state, runtime
