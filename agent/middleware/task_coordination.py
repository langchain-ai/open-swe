import logging
from collections.abc import Awaitable, Callable
from typing import Self

import langgraph_sdk
from langchain.agents.middleware import AgentState
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent.database import postgres
from agent.github.proxy import maybe_refresh_proxy_token
from agent.input_messages import RunMessage
from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import prompt
from agent.tasks.flags import task_coordination_enabled, task_coordination_supported
from agent.tasks.messages import TaskMessage
from agent.tasks.schemas import Thread
from agent.tasks.store import TaskContext, TaskDelegation, TaskMembership

logger = logging.getLogger(__name__)


class TaskCoordinationMiddleware(OpenSWEMiddleware):
    def __init__(
        self, thread_id: str, owner_login: str, enabled: bool, context: TaskContext | None
    ) -> None:
        self.thread_id = thread_id
        self.owner_login = owner_login
        self.enabled = enabled
        self.context = context
        self.instructions = prompt(
            "tasks/role",
            role=context.membership.role if context else "coordinator",
            delegation_enabled=enabled,
        )

    @classmethod
    async def for_thread(cls, thread_id: str) -> Self | None:
        thread = Thread.model_validate(await langgraph_sdk.get_client().threads.get(thread_id))
        metadata = thread.metadata
        owner_login = metadata.owner_login if metadata.owner_type == "user" else None
        enabled = task_coordination_supported(
            metadata.json_metadata()
        ) and await task_coordination_enabled(
            owner_login or "", owner_user_id=metadata.owner_user_id
        )
        context = await TaskMembership.context_for_thread(thread_id)
        if context is not None and context.membership.role == "worker":
            delegation = await TaskDelegation.get(thread_id)
            if delegation is not None and delegation.cancelled:
                raise PermissionError(
                    "This worker was cancelled; create a new worker for further work"
                )
        return cls(thread_id, owner_login or "", enabled, context) if enabled or context else None

    @property
    def is_worker(self) -> bool:
        return self.context is not None and self.context.membership.role == "worker"

    async def abefore_model(
        self, state: AgentState, runtime: Runtime
    ) -> dict[str, list[RunMessage]] | None:
        if self.context is not None and self.is_worker:
            try:
                await maybe_refresh_proxy_token(self.context.task.require_coordinator())
            except Exception:
                logger.warning(
                    "Failed to refresh the task's GitHub proxy",
                    exc_info=True,
                    extra={"thread_id": self.thread_id},
                )
        if not postgres.configured():
            return None
        owed = await TaskMessage.owed(self.thread_id, state["messages"])
        if owed:
            return {"messages": TaskMessage.messages(owed)}
        return None

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if self.context is None:
            self.context = await TaskMembership.context_for_thread(self.thread_id)
        blocks = list(request.system_message.content_blocks) if request.system_message else []
        blocks.append({"type": "text", "text": self.instructions})
        messages = list(request.messages)
        if self.context is not None:
            messages.append(
                HumanMessage(
                    content=prompt(
                        "tasks/context",
                        task_id=str(self.context.task.id),
                        title=self.context.task.title,
                        coordinator_thread_id=self.context.task.coordinator_thread_id,
                    )
                )
            )
        return await handler(
            request.override(system_message=SystemMessage(content_blocks=blocks), messages=messages)
        )
