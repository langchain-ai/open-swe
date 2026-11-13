"""Best-effort automation token budgets in checkpointed thread state."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, NotRequired

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.types import OmitFromInput, OmitFromOutput, hook_config
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime
from runtime_api.catalog import AUTOMATION_DAILY_TOKENS
from runtime_api.loop import AutomationUsage

from rt.backend import OpenSweBackend


class AutomationBudgetState(AgentState):
    automation_usage_day: NotRequired[Annotated[str, OmitFromInput, OmitFromOutput]]
    automation_usage_tokens: NotRequired[Annotated[int, OmitFromInput, OmitFromOutput]]


class AutomationBudgetMiddleware(AgentMiddleware[AutomationBudgetState]):
    """Stop non-admin automation threads after their best-effort UTC daily budget."""

    state_schema = AutomationBudgetState

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend

    @staticmethod
    def usage(state: Mapping[str, object]) -> AutomationUsage | None:
        """The thread's counter, for the run's end to report; None where nothing counted."""
        day = state.get("automation_usage_day")
        tokens = state.get("automation_usage_tokens")
        if not isinstance(day, str) or not isinstance(tokens, int):
            return None
        return AutomationUsage(day=day, tokens=tokens)

    @hook_config(can_jump_to=["end"])
    async def abefore_model(
        self, state: AutomationBudgetState, runtime: Runtime
    ) -> dict[str, object] | None:
        if (
            self._backend.run.catalog.loop.pinned_model
            and state.get("automation_usage_day") == datetime.now(UTC).date().isoformat()
            and state.get("automation_usage_tokens", 0) >= AUTOMATION_DAILY_TOKENS
        ):
            return {"jump_to": "end"}
        return None

    async def aafter_model(
        self, state: AutomationBudgetState, runtime: Runtime
    ) -> dict[str, object] | None:
        if not self._backend.run.catalog.loop.pinned_model:
            return None
        day = datetime.now(UTC).date().isoformat()
        used = (
            state.get("automation_usage_tokens", 0)
            if state.get("automation_usage_day") == day
            else 0
        )
        message = next((m for m in reversed(state["messages"]) if isinstance(m, AIMessage)), None)
        tokens = message.usage_metadata["total_tokens"] if message and message.usage_metadata else 0
        return {"automation_usage_day": day, "automation_usage_tokens": used + tokens}
