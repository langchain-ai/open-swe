from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NotRequired, cast

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime


class PrepareRunState(AgentState):
    run_prepared: NotRequired[bool]
    run_prepared_for: NotRequired[str]
    work_dir: NotRequired[str | None]
    rendered_system_prompt: NotRequired[str | None]


def _format_prepare_failure(exc: BaseException) -> str:
    return (
        "Run setup failed before the agent could start "
        f"({exc.__class__.__name__}: {exc}). This is usually a transient "
        "sandbox or GitHub-token initialization error; retry the run."
    )


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


class BasePrepareRunMiddleware(AgentMiddleware):
    """Checkpointed per-run setup.

    Subclasses must keep `_prepare` idempotent. LangGraph checkpoints the
    `run_prepared_for` latch after this before-agent node, so resumed attempts
    of the same invocation skip completed setup while later invocations on the
    same thread re-prepare fresh tokens, prompts, and diff context. If a run
    fails before that checkpoint, setup may execute again and every operation it
    calls must tolerate that.
    """

    state_schema = PrepareRunState

    async def abefore_agent(
        self,
        state: AgentState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        prepared_state = cast(PrepareRunState, state)
        fingerprint = self._prepare_fingerprint(prepared_state, runtime)
        if (
            prepared_state.get("run_prepared")
            and prepared_state.get("run_prepared_for") == fingerprint
        ):
            return None
        try:
            updates = await self._prepare(prepared_state, runtime)
        except Exception as exc:  # surface instead of aborting with zero trajectory
            return {
                "run_prepared": False,
                "messages": [SystemMessage(content=_format_prepare_failure(exc))],
            }
        return {"run_prepared": True, "run_prepared_for": fingerprint, **updates}

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

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        rendered = request.state.get("rendered_system_prompt")
        if isinstance(rendered, str) and rendered:
            existing = request.system_message.text if request.system_message is not None else ""
            content = f"{rendered}\n\n{existing}" if existing else rendered
            request = request.override(system_message=SystemMessage(content=content))
        return await handler(request)
