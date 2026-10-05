from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.config import get_config

from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import prompt
from agent.tasks.store import load_context


class TaskCoordinationMiddleware(OpenSWEMiddleware):
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        thread_id = get_config().get("configurable", {}).get("thread_id")
        context = await load_context(thread_id) if isinstance(thread_id, str) else None
        if context is None:
            return await handler(request)
        instructions = prompt(
            "tasks/role",
            task_id=str(context.task.id),
            title=context.task.title,
            criteria=context.task.acceptance_criteria,
            coordinator_thread_id=context.task.coordinator_thread_id,
            role=context.membership.role,
            delegated=context.task.delegated,
            status=context.task.status,
            revision=context.task.revision,
        )
        blocks = list(request.system_message.content_blocks) if request.system_message else []
        blocks.append({"type": "text", "text": instructions})
        return await handler(request.override(system_message=SystemMessage(content_blocks=blocks)))
