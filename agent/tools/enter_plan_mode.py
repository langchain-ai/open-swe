"""Tool: ``enter_plan_mode``. Switch the run into read-only planning."""

import logging
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.config import get_config
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from langgraph_sdk import get_client

from ..dashboard.plan_store import PLAN_STATUS_PLANNING, set_plan_status
from ..utils.sandbox_state import get_sandbox_backend
from ..utils.turn_checkpoint import mark_checkpoint_plan_mode, record_plan_checkpoint

logger = logging.getLogger(__name__)

_ENTERED_MESSAGE = (
    "Plan mode is active. Stay read-only for the target repo: research the codebase, "
    "create or edit a dated, concise plan file under `/workspace/plans/`, then publish "
    "it with the `save_plan` tool and share the plan-review link in the source channel. "
    "Do not edit repo files, commit, push, or open a PR — wait for the user to approve "
    "the plan."
)


async def enter_plan_mode(
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict[str, Any] | None, InjectedState] = None,
) -> Command:
    """Activate plan mode mid-run.

    Call this when you believe the task would benefit from a structured
    implementation plan before writing any code — e.g. when the request is
    complex, touches many files, or has multiple valid approaches. This is
    NOT triggered by the word "plan" appearing in the request; use your
    judgment about whether planning is genuinely warranted.

    Once activated, stay read-only for the target repo: research the codebase,
    create or edit a dated, concise Markdown plan outside any repo (for example,
    ``/workspace/plans/YYYY-MM-DD-short-task-slug.md``), then publish it with
    the ``save_plan`` tool and share the plan-review link with the user. Do not
    edit repo files, commit, push, or open a PR — the user reviews the plan and
    approves it before you implement.
    """
    thread_id = _thread_id_from_config()
    if thread_id:
        try:
            await set_plan_status(thread_id, PLAN_STATUS_PLANNING, plan_mode=True)
            turn_key = _latest_human_message_id(state)
            if turn_key:
                await _mark_turn_checkpoint(thread_id, turn_key)
        except Exception:
            logger.warning("Failed to persist plan-mode entry for %s", thread_id, exc_info=True)
    return Command(
        update={
            "plan_mode": True,
            "messages": [ToolMessage(content=_ENTERED_MESSAGE, tool_call_id=tool_call_id)],
        }
    )


def _latest_human_message_id(state: dict[str, Any] | None) -> str | None:
    if not isinstance(state, dict):
        return None
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage) and message.id:
            return message.id
    return None


async def _mark_turn_checkpoint(thread_id: str, turn_key: str) -> None:
    client = get_client()
    thread = await client.threads.get(thread_id)
    metadata = (
        thread.get("metadata") if isinstance(thread, dict) else getattr(thread, "metadata", None)
    )
    metadata = metadata if isinstance(metadata, dict) else {}
    existing = metadata.get("turn_checkpoints")
    checkpoint = next(
        (
            entry
            for entry in (existing if isinstance(existing, list) else [])
            if isinstance(entry, dict) and entry.get("key") == turn_key
        ),
        None,
    )
    plan_ref = None
    if checkpoint:
        backend = await get_sandbox_backend(thread_id)
        repo_path = checkpoint.get("repo_path")
        plan_ref = await record_plan_checkpoint(
            backend,
            None,
            turn_key,
            repo_path=repo_path if isinstance(repo_path, str) else None,
        )
    updated = mark_checkpoint_plan_mode(existing, turn_key, plan_ref)
    if updated != existing:
        await client.threads.update(
            thread_id=thread_id,
            metadata={"turn_checkpoints": updated},
        )


def _thread_id_from_config() -> str | None:
    try:
        config = get_config()
    except Exception:
        return None
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    return str(thread_id) if thread_id else None
