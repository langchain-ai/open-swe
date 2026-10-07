"""Return worker invocation outcomes through the existing durable event path."""

from collections.abc import Mapping
from typing import cast
from uuid import UUID

from agent.invocation import resolve_invocation_id
from agent.prompts import prompt
from agent.tasks import presentation, store
from agent.tasks.presentation import TaskEventMetadata, TaskEventStatus
from agent.utils.dashboard_links import dashboard_thread_url
from agent.utils.thread_ops import langgraph_client
from agent.webhooks.event_subscriptions import EventSubscription

_TERMINAL_STATUSES = frozenset({"success", "error", "timeout", "interrupted"})
_MAX_RESULT_CHARS = 16_000


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            block["text"]
            for block in value
            if isinstance(block, Mapping) and isinstance(block.get("text"), str)
        )
    return ""


def _answer(values: object) -> str:
    if not isinstance(values, Mapping):
        return ""
    messages = values.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, Mapping):
            continue
        role = message.get("type", message.get("role"))
        if role in {"human", "user"}:
            break
        if role in {"ai", "assistant"} and not message.get("tool_calls"):
            answer = _text(message.get("content")).strip()
            if answer:
                return answer[:_MAX_RESULT_CHARS]
    return ""


def _error(status: str, error: object) -> str:
    if isinstance(error, Mapping):
        detail = ": ".join(
            value for key in ("error", "message") if isinstance(value := error.get(key), str)
        )
    else:
        detail = error if isinstance(error, str) else ""
    return (detail.strip() or f"Worker invocation ended with status {status}.")[:_MAX_RESULT_CHARS]


async def worker_result(
    thread_id: str, run_id: str, status: str, payload: Mapping[str, object]
) -> str:
    if status != "success":
        return _error(status, payload.get("error"))
    if answer := _answer(payload.get("values")):
        return answer
    metadata = payload.get("metadata")
    invocation_id = resolve_invocation_id(metadata if isinstance(metadata, Mapping) else None)
    client = langgraph_client()
    if invocation_id is None:
        run = await client.runs.get(thread_id, run_id)
        invocation_id = resolve_invocation_id(run.get("metadata"))
    history = await client.threads.get_history(
        thread_id,
        limit=1,
        metadata={"invocation_id": invocation_id} if invocation_id else {"run_id": run_id},
    )
    if history and (answer := _answer(history[0].get("values"))):
        return answer
    return "The invocation completed without a final answer. Inspect the worker thread for details."


async def worker_finished(
    thread_id: str, run_id: str, status: str, payload: Mapping[str, object]
) -> bool:
    context = await store.load_context(thread_id)
    if context is None or context.membership.role != "worker":
        return False
    if status not in _TERMINAL_STATUSES:
        return True
    from agent.tasks.service import notify

    delegation = await store.get_delegation(thread_id)
    if delegation is not None and delegation.cancelled and status != "interrupted":
        return True
    result = await worker_result(thread_id, run_id, status, payload)
    await notify(
        context.task,
        context.task.coordinator_thread_id,
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
    delegation = await store.get_delegation(thread_id)
    if status != "interrupted" and delegation is not None and not delegation.cancelled:
        await EventSubscription.deliver_to(thread_id, "enqueue")
    return True
