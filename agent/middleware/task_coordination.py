import logging
from collections.abc import Awaitable, Callable
from typing import Self

import langgraph_sdk
from langchain.agents.middleware import AgentState
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from agent.github.proxy import maybe_refresh_proxy_token
from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import prompt
from agent.tasks.flags import (
    task_coordination_enabled,
    task_coordination_supported,
    task_owner_login,
)
from agent.tasks.store import TaskContext, get_delegation, load_context
from agent.utils.json_types import thread_metadata

logger = logging.getLogger(__name__)


class TaskCoordinationMiddleware(OpenSWEMiddleware):
    def __init__(
        self, thread_id: str, owner_login: str, enabled: bool, context: TaskContext | None
    ) -> None:
        self.thread_id = thread_id
        self.owner_login = owner_login
        self.enabled = enabled
        self.context = context

    @classmethod
    async def for_thread(cls, thread_id: str) -> Self | None:
        metadata = thread_metadata(await langgraph_sdk.get_client().threads.get(thread_id))
        owner_login = task_owner_login(metadata)
        enabled = task_coordination_supported(metadata) and await task_coordination_enabled(
            owner_login
        )
        context = await load_context(thread_id)
        if context is not None and context.membership.role == "worker":
            delegation = await get_delegation(thread_id)
            if delegation is not None and delegation.cancelled:
                raise PermissionError(
                    "This worker was cancelled; create a new worker for further work"
                )
        return cls(thread_id, owner_login, enabled, context) if enabled or context else None

    @property
    def is_worker(self) -> bool:
        return self.context is not None and self.context.membership.role == "worker"

    async def abefore_model(self, state: AgentState, runtime: Runtime) -> None:
        if self.context is not None and self.is_worker:
            try:
                await maybe_refresh_proxy_token(self.context.task.coordinator_thread_id)
            except Exception:
                logger.warning(
                    "Failed to refresh the task's GitHub proxy",
                    exc_info=True,
                    extra={"thread_id": self.thread_id},
                )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        # A thread becomes a coordinator only after its first spawn in this run.
        if self.context is None:
            self.context = await load_context(self.thread_id)
        context = self.context
        if context is None:
            return await handler(request)
        instructions = prompt(
            "tasks/role",
            task_id=str(context.task.id),
            title=context.task.title,
            coordinator_thread_id=context.task.coordinator_thread_id,
            role=context.membership.role,
            delegated=context.task.delegated,
            delegation_enabled=self.enabled and await task_coordination_enabled(self.owner_login),
        )
        blocks = list(request.system_message.content_blocks) if request.system_message else []
        blocks.append({"type": "text", "text": instructions})
        return await handler(request.override(system_message=SystemMessage(content_blocks=blocks)))
