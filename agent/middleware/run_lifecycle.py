"""Write graph lifecycle transitions and finalized messages to the registry."""

import logging
from collections.abc import Awaitable, Callable, Mapping

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..dashboard.thread_registry import get_thread_registry
from ..dashboard.thread_transcript import messages_to_ui

logger = logging.getLogger(__name__)


def _run_id() -> str | None:
    config = get_config()
    candidates = [config.get("run_id")]
    configurable = config.get("configurable")
    if isinstance(configurable, Mapping):
        candidates.append(configurable.get("run_id"))
    return next((str(value) for value in candidates if value), None)


class RunLifecycleMiddleware(AgentMiddleware):
    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self._started_runs: set[str] = set()

    async def _transition(self, status: str, *, error: str | None = None) -> None:
        run_id = _run_id()
        if not run_id:
            return
        try:
            registry = await get_thread_registry()
            await registry.transition(
                self.thread_id,
                run_id,
                status,  # type: ignore[arg-type]
                environment="cloud",
                error=error,
            )
        except (KeyError, ValueError):
            logger.debug(
                "Skipped stale lifecycle transition %s for %s/%s",
                status,
                self.thread_id,
                run_id,
            )
        except Exception:
            logger.exception("Could not record lifecycle transition for %s", self.thread_id)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        run_id = _run_id()
        if run_id and run_id not in self._started_runs:
            await self._transition("running")
            self._started_runs.add(run_id)
        try:
            return await handler(request)
        except BaseException as exc:
            await self._transition("error", error=type(exc).__name__)
            raise

    async def aafter_agent(
        self,
        state: AgentState,
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, object] | None:
        run_id = _run_id()
        if not run_id:
            return None
        try:
            registry = await get_thread_registry()
            await registry.append_messages(
                self.thread_id,
                run_id,
                messages_to_ui(state.get("messages")),
            )
        except Exception:
            logger.exception("Could not persist transcript for %s", self.thread_id)
        await self._transition("finished")
        return None
