import logging
from collections.abc import Mapping
from typing import Self
from uuid import uuid4

from langchain.agents.middleware import AgentState
from langgraph.runtime import Runtime

from openswe.database import postgres
from openswe.github.proxy import maybe_refresh_proxy_token
from openswe.input_messages import RunMessage, dynamic_context_hash, visible_dynamic_context_hashes
from openswe.middleware.model_call_shape import CallShape
from openswe.middleware.trace import OpenSWEMiddleware
from openswe.prompts import prompt
from openswe.tasks.messages import TaskMessage
from openswe.tasks.schemas import Thread
from openswe.tasks.store import TaskContext, TaskDelegation, TaskMembership
from openswe.utils.thread_reads import read_thread

logger = logging.getLogger(__name__)


class TaskCoordinationMiddleware(OpenSWEMiddleware):
    def __init__(self, thread_id: str, enabled: bool, context: TaskContext | None) -> None:
        self.thread_id = thread_id
        self.enabled = enabled
        self.context = context
        self.instructions = prompt(
            "tasks/role",
            role=context.membership.role if context else "coordinator",
            delegation_enabled=self.can_delegate,
        )

    @classmethod
    async def for_thread(cls, thread_id: str) -> Self | None:
        enabled = await Thread.model_validate(
            await read_thread(thread_id)
        ).metadata.delegation_supported()
        context = await TaskMembership.context_for_thread(thread_id)
        if context is not None and context.membership.role == "worker":
            delegation = await TaskDelegation.get(thread_id)
            if delegation is not None and delegation.cancelled:
                raise PermissionError(
                    "This worker was cancelled; create a new worker for further work"
                )
        return cls(thread_id, enabled, context) if enabled or context else None

    @property
    def is_worker(self) -> bool:
        return self.context is not None and self.context.membership.role == "worker"

    @property
    def can_delegate(self) -> bool:
        return self.enabled and not self.is_worker

    async def abefore_model(
        self, state: AgentState, runtime: Runtime
    ) -> dict[str, list[RunMessage]] | None:
        if self.context is None:
            self.context = await TaskMembership.context_for_thread(self.thread_id)
        if self.context is not None and self.is_worker:
            try:
                await maybe_refresh_proxy_token(self.context.task.require_coordinator())
            except Exception:
                logger.warning(
                    "Failed to refresh the task's GitHub proxy",
                    exc_info=True,
                    extra={"thread_id": self.thread_id},
                )
        messages: list[RunMessage] = []
        if self.context is not None:
            content = prompt(
                "tasks/context",
                task_id=str(self.context.task.id),
                title=self.context.task.title,
                coordinator_thread_id=self.context.task.coordinator_thread_id,
            )
            if dynamic_context_hash(content) not in visible_dynamic_context_hashes(state):
                messages.append(
                    {"role": "user", "id": f"task-context:{uuid4()}", "content": content}
                )
        if postgres.configured():
            owed = await TaskMessage.owed(self.thread_id, state["messages"])
            messages.extend(TaskMessage.messages(owed))
        return {"messages": messages} if messages else None

    async def ashape_model_call(
        self, call: CallShape, state: Mapping[str, object], runtime: Runtime
    ) -> CallShape:
        return call.appending(self.instructions)
