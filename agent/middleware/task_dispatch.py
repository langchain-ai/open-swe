import logging

from langchain.agents.middleware.types import AgentState, hook_config
from langgraph.config import get_config
from langgraph.runtime import Runtime

from agent.invocation import resolve_invocation_id
from agent.middleware.trace import OpenSWEMiddleware
from agent.tasks import store

logger = logging.getLogger(__name__)


class TaskDispatchMiddleware(OpenSWEMiddleware[AgentState]):
    def __init__(self, thread_id: str) -> None:
        self._thread_id = thread_id

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, object] | None:
        config = get_config()
        metadata = config.get("metadata") or {}
        dispatch_key = metadata.get("task_dispatch_key")
        if dispatch_key is None:
            return None
        if not isinstance(dispatch_key, str) or not dispatch_key:
            raise ValueError("A task dispatch key must be a nonempty string")
        invocation_id = resolve_invocation_id(config.get("configurable"), metadata)
        if invocation_id is None:
            raise ValueError("A task dispatch requires an invocation identity")
        if await store.claim_task_dispatch(self._thread_id, dispatch_key, invocation_id):
            return None
        logger.info(
            "Duplicate task dispatch skipped",
            extra={
                "agent_thread_id": self._thread_id,
                "task_dispatch_key": dispatch_key,
                "invocation_id": invocation_id,
            },
        )
        return {"jump_to": "end"}
