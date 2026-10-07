"""Return worker invocation outcomes through the existing durable event path."""

import logging
from collections.abc import Mapping
from typing import cast
from uuid import UUID

from openswe.prompts import prompt
from openswe.tasks import presentation, store
from openswe.tasks.messages import TaskMessage
from openswe.tasks.presentation import TaskEventMetadata, TaskEventStatus
from openswe.tasks.schemas import RunPayload, StateSnapshot
from openswe.utils.dashboard_links import dashboard_thread_url
from openswe.utils.thread_ops import langgraph_client
from openswe.webhooks.event_subscriptions import EventSubscription

logger = logging.getLogger(__name__)
_TERMINAL_STATUSES = frozenset({"success", "error", "timeout", "interrupted"})
_MAX_RESULT_CHARS = 16_000


async def worker_result(
    thread_id: str, run_id: str, status: str, payload: Mapping[str, object]
) -> str:
    parsed = RunPayload.parse(payload)
    if status != "success":
        return parsed.failure(status, _MAX_RESULT_CHARS)
    if parsed.values is not None and (answer := parsed.values.answer(_MAX_RESULT_CHARS)):
        return answer
    invocation_id = parsed.metadata.invocation()
    client = langgraph_client()
    if invocation_id is None:
        run = RunPayload.parse(await client.runs.get(thread_id, run_id))
        invocation_id = run.metadata.invocation()
    history = await client.threads.get_history(
        thread_id,
        limit=1,
        metadata={"invocation_id": invocation_id} if invocation_id else {"run_id": run_id},
    )
    if history:
        state = StateSnapshot.model_validate(history[0])
        if answer := state.values.answer(_MAX_RESULT_CHARS):
            return answer
    return "The invocation completed without a final answer. Inspect the worker thread for details."


async def worker_finished(
    thread_id: str, run_id: str, status: str, payload: Mapping[str, object]
) -> bool:
    context = await store.TaskMembership.context_for_thread(thread_id)
    if context is None or context.membership.role != "worker":
        return False
    if status not in _TERMINAL_STATUSES:
        return True
    from openswe.tasks.service import notify

    delegation = await store.TaskDelegation.get(thread_id)
    if delegation is not None and delegation.cancelled and status != "interrupted":
        logger.info(
            "Suppressed a cancelled worker's run outcome",
            extra={"worker_thread_id": thread_id, "run_id": run_id, "run_status": status},
        )
        return True
    try:
        result = await worker_result(thread_id, run_id, status, payload)
    except Exception:
        # Losing the answer must not lose the hand-off: the coordinator still learns it finished.
        logger.exception(
            "Could not read the worker's final answer",
            extra={"worker_thread_id": thread_id, "run_id": run_id},
        )
        result = "The worker finished, but its final answer could not be read. Inspect the worker thread."
    await notify(
        context.task,
        context.task.require_coordinator(),
        f"finished:{thread_id}:{run_id}",
        prompt(
            "tasks/finished",
            task_id=str(context.task.id),
            worker_id=thread_id,
            worker_url=dashboard_thread_url(thread_id),
            run_id=run_id,
            status=status,
            result=result,
        ),
        task_event=TaskEventMetadata(
            task_id=context.task.id,
            sender_thread_id=UUID(thread_id),
            sender_role="worker",
            sender_label=await presentation.sender_label(thread_id),
            kind="completion",
            status=cast(TaskEventStatus, status),
            content=result,
        ),
    )
    delegation = await store.TaskDelegation.get(thread_id)
    if status != "interrupted" and delegation is not None and not delegation.cancelled:
        await TaskMessage.deliver_to(thread_id)
        await EventSubscription.deliver_to(thread_id, "enqueue")
    return True
