"""Tools: ``enter_plan_mode``, ``save_plan``, ``approve_plan``."""

import logging
import re
from collections.abc import Mapping
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from typing_extensions import TypedDict

from coding_agent.plans import (
    PLAN_FILE_DIRECTORY,
    PlanNotApprovable,
    PlanStore,
    PlanStoreFactory,
)
from coding_agent.run_config import RunConfig
from coding_agent.sandboxes.state import get_sandbox_backend
from coding_agent.utils.html_artifact import DEFAULT_TITLE, wrap_html_artifact

logger = logging.getLogger(__name__)

_MAX_PLAN_LINES = 20_000

_ENTERED_MESSAGE = (
    "Plan mode is active. Stay read-only for the target repo: research the codebase, "
    "create or edit a dated, self-contained HTML artifact under `/workspace/plans/`, then "
    "publish it with the `save_plan` tool and share the plan-review link in the source "
    "channel. Do not edit repo files, commit, push, or open a PR — wait for the user to "
    "approve the plan."
)


class PlanModeState(TypedDict, total=False):
    plan_mode: bool


def plan_tools(plans: PlanStoreFactory) -> list[Any]:
    """The plan-mode tools, bound to the platform's plan store."""

    async def enter_plan_mode(
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Implement the `enter_plan_mode` tool."""
        thread_id = RunConfig.from_runtime().thread_id or None
        if thread_id:
            try:
                await plans(thread_id).begin()
            except Exception:
                logger.warning("Failed to persist plan-mode entry for %s", thread_id, exc_info=True)
        return Command(
            update={
                "plan_mode": True,
                "messages": [ToolMessage(content=_ENTERED_MESSAGE, tool_call_id=tool_call_id)],
            }
        )

    async def save_plan(
        plan_file_path: str,
        state: Annotated[PlanModeState | None, InjectedState] = None,
    ) -> dict[str, Any]:
        """Implement the `save_plan` tool."""
        if not isinstance(plan_file_path, str):
            return {"success": False, "error": "plan_file_path must be a string"}
        path = plan_file_path.strip()
        if not path:
            return {"success": False, "error": "plan_file_path cannot be empty"}
        if not _is_html_path(path):
            return {
                "success": False,
                "error": f"plan_file_path must point to an HTML file in {PLAN_FILE_DIRECTORY}",
            }

        cfg = RunConfig.from_runtime()
        thread_id = cfg.thread_id
        if not thread_id:
            return {"success": False, "error": "no thread_id in run config"}

        try:
            content = (await _read_plan_file(str(thread_id), path)).strip()
            if not content:
                return {"success": False, "error": "plan file cannot be empty"}
            await plans(str(thread_id)).publish(
                document=wrap_html_artifact(content, title=_title_from_path(path)),
                source_path=path,
                plan_mode=_state_plan_mode(state) or cfg.plan_mode is True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("save_plan failed for thread %s", thread_id)
            return {"success": False, "error": f"failed to save plan: {exc}"}
        return {"success": True, "path": path}

    async def approve_plan(
        state: Annotated[PlanModeState | None, InjectedState] = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command | dict[str, Any]:
        """Implement the `approve_plan` tool."""
        cfg = RunConfig.from_runtime()
        thread_id = cfg.thread_id
        if not thread_id:
            return {"success": False, "error": "no thread_id in run config"}

        store = plans(str(thread_id))
        try:
            if not await _plan_mode_is_active(state, cfg, store):
                return {"success": False, "error": "plan mode is not active for this thread"}
            approved = await store.approve()
        except PlanNotApprovable as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("approve_plan failed for thread %s", thread_id)
            return {"success": False, "error": f"failed to approve plan: {exc}"}

        return Command(
            update={
                "plan_mode": False,
                "messages": [
                    ToolMessage(
                        content=_approved_message(approved.document, approved.reviewer_feedback),
                        tool_call_id=tool_call_id,
                    )
                ],
            }
        )

    return [enter_plan_mode, save_plan, approve_plan]


def _state_plan_mode(state: Mapping[str, Any] | None) -> bool:
    return isinstance(state, dict) and state.get("plan_mode") is True


async def _plan_mode_is_active(
    state: Mapping[str, Any] | None,
    cfg: RunConfig,
    store: PlanStore,
) -> bool:
    if isinstance(state, dict) and "plan_mode" in state:
        return state.get("plan_mode") is True
    if cfg.plan_mode is True:
        return True
    return await store.is_active()


async def _read_plan_file(thread_id: str, path: str) -> str:
    backend = await get_sandbox_backend(thread_id)
    result = await backend.aread(path, offset=0, limit=_MAX_PLAN_LINES)
    error = _value(result, "error")
    if error:
        raise ValueError(error)
    file_data = _value(result, "file_data")
    if file_data is None:
        raise ValueError("plan file could not be read")
    encoding = _value(file_data, "encoding")
    if encoding is not None and encoding != "utf-8":
        raise ValueError("plan file must be UTF-8 text")
    content = _value(file_data, "content")
    if not isinstance(content, str):
        raise ValueError("plan file content was not text")
    if content.count("\n") + 1 >= _MAX_PLAN_LINES:
        raise ValueError("plan file is too large")
    return content


def _value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _is_html_path(path: str) -> bool:
    if "\x00" in path or not path.startswith(f"{PLAN_FILE_DIRECTORY}/"):
        return False
    filename = path.removeprefix(f"{PLAN_FILE_DIRECTORY}/")
    return bool(filename and "/" not in filename and filename.lower().endswith(".html"))


def _title_from_path(path: str) -> str:
    """Fallback artifact name for a plan file that carries no title of its own."""
    stem = path.rsplit("/", 1)[-1].removesuffix(".html")
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", stem)
    words = stem.replace("-", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else DEFAULT_TITLE


def _approved_message(plan: str, feedback: str) -> str:
    if plan:
        message = (
            "Plan mode is now inactive because the plan was approved. Use the reviewed plan "
            "below as the implementation guide. Apply reasonable engineering judgment where "
            "details need adjustment while preserving its goals and reviewer edits:\n\n"
            f"{plan}"
        )
    else:
        message = (
            "Plan mode is now inactive because the plan was approved. "
            "Implement now as described in the approved plan."
        )
    if feedback:
        message += "\n\nAlso take this reviewer feedback into account:\n\n" + feedback
    return message
