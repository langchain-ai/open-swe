"""Deliver channel messages to a running agent at its next model call."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import Any, cast

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ResponseT,
    TracePolicy,
    hook_config,
    omit_payload,
)
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime
from langgraph_sdk.client import LangGraphClient
from langgraph_sdk.client import get_client as _get_client
from langgraph_sdk.schema import RunSelectField
from pydantic import ValidationError

from managed_deepagents._ingress_runs import TAKEABLE_METADATA_KEY

__all__ = ["TAKEABLE_METADATA_KEY", "SteeringMiddleware"]

logger = logging.getLogger(__name__)

_RUN_SELECT: list[RunSelectField] = [
    "run_id",
    "created_at",
    "metadata",
    "kwargs",
    "multitask_strategy",
]
_RUN_LIST_LIMIT = 100
_USER_ID_CONFIG_KEY = "langgraph_auth_user_id"


def is_top_level_graph(config: Mapping[str, Any]) -> bool:
    """Whether the hook runs in the run's outermost graph, not a nested subagent graph."""
    return "|" not in str((config.get("configurable") or {}).get("checkpoint_ns") or "")


def _configurable(kwargs: Mapping[str, Any]) -> Mapping[str, Any]:
    config = kwargs.get("config")
    configurable = config.get("configurable") if isinstance(config, Mapping) else None
    return configurable if isinstance(configurable, Mapping) else {}


def _status_code(error: Exception) -> int | None:
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    status = getattr(error, "status_code", None)
    return status if isinstance(status, int) else None


def _run_id() -> str | None:
    run_id = (get_config().get("metadata") or {}).get("run_id")
    return str(run_id) if run_id is not None else None


class SteeringMiddleware(AgentMiddleware[AgentState, ContextT, ResponseT]):
    """Add messages queued on the thread to the running agent's conversation.

    Before each model call, and after a final answer, takes the thread's
    takeable queued runs: cancels each with ``reason=taken`` and adds its user
    messages to state. A run is takeable when ingress marked it with
    ``TAKEABLE_METADATA_KEY``, it uses ``enqueue``, holds only user messages, and
    runs as the same principal as the active run. Runs not taken execute as
    usual. Async only, outermost graph only.

    Requires ``langgraph-api`` 0.19 or later to record ``reason=taken``.

    !!! warning "Beta"

        Steering may change.
    """

    trace_policy = TracePolicy(process_inputs=omit_payload)

    def __init__(self) -> None:
        """Initialize the ``SteeringMiddleware``."""
        super().__init__()
        self._async: LangGraphClient | None = None
        self._warned: set[str] = set()
        self._failed_in: dict[str, str | None] = {}

    def before_model(
        self,
        state: AgentState,  # noqa: ARG002
        runtime: Runtime[ContextT],  # noqa: ARG002
    ) -> dict[str, Any] | None:
        return None

    async def abefore_model(
        self,
        state: AgentState,  # noqa: ARG002
        runtime: Runtime[ContextT],  # noqa: ARG002
    ) -> dict[str, Any] | None:
        thread_id = self._thread_id()
        if thread_id is None:
            return None
        taken = await self._atake(thread_id)
        return {"messages": taken} if taken else None

    @hook_config(can_jump_to=["model"])
    def after_model(
        self,
        state: AgentState,  # noqa: ARG002
        runtime: Runtime[ContextT],  # noqa: ARG002
    ) -> dict[str, Any] | None:
        return None

    @hook_config(can_jump_to=["model"])
    async def aafter_model(
        self,
        state: AgentState,
        runtime: Runtime[ContextT],  # noqa: ARG002
    ) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage) or last.tool_calls:
            return None
        thread_id = self._thread_id()
        if thread_id is None:
            return None
        late = await self._atake(thread_id)
        return {"messages": late, "jump_to": "model"} if late else None

    def _thread_id(self) -> str | None:
        config = get_config()
        thread_id = (config.get("configurable") or {}).get("thread_id")
        if thread_id is None:
            return None
        if not is_top_level_graph(config):
            self._warn_once("nested", "SteeringMiddleware takes no messages in a nested graph")
            return None
        return str(thread_id)

    async def _atake(self, thread_id: str) -> list[HumanMessage]:
        user_id = (get_config().get("configurable") or {}).get(_USER_ID_CONFIG_KEY)
        if not (isinstance(user_id, str) and user_id):
            self._warn_once("principal", "SteeringMiddleware takes no messages without a principal")
            return []
        if self._async is None:
            self._async = _get_client()
        try:
            waiting = cast(
                "list[dict[str, Any]]",
                await self._async.runs.list(
                    thread_id=thread_id, status="pending", select=_RUN_SELECT, limit=_RUN_LIST_LIMIT
                ),
            )
        except Exception as e:  # noqa: BLE001
            self._warn_failed("list", e)
            return []
        taken: list[HumanMessage] = []
        taken_runs: list[str] = []
        for run, messages in _takeable_runs(waiting, user_id):
            try:
                await self._async.runs.cancel(thread_id, run["run_id"], params={"reason": "taken"})
            except Exception as e:  # noqa: BLE001
                if _status_code(e) == HTTPStatus.NOT_FOUND:
                    continue  # started or ended first
                self._warn_failed("cancel", e)
                break  # keep the rest in order for the next call
            taken.extend(messages)
            taken_runs.append(str(run["run_id"]))
        if taken_runs:
            logger.info(
                "Steering took queued run(s) %s into run %s on thread %s",
                ", ".join(taken_runs),
                _run_id(),
                thread_id,
            )
        return taken

    def _warn_once(self, problem: str, message: str) -> None:
        if problem not in self._warned:
            self._warned.add(problem)
            logger.warning(message)

    def _warn_failed(self, problem: str, error: Exception) -> None:
        run_id = _run_id()
        if problem not in self._failed_in or self._failed_in[problem] != run_id:
            self._failed_in[problem] = run_id
            logger.warning(
                "Steering failed; messages wait as queued runs until it works again",
                exc_info=error,
            )


def _takeable_runs(
    waiting: Sequence[Mapping[str, Any]], principal: str
) -> list[tuple[Mapping[str, Any], list[HumanMessage]]]:
    runs = [
        (run, messages)
        for run in waiting
        if (messages := _takeable_messages(run, principal)) is not None
    ]
    runs.sort(key=lambda pair: str(pair[0].get("created_at") or ""))
    return runs


def _takeable_messages(run: Mapping[str, Any], principal: str) -> list[HumanMessage] | None:
    """The run's user messages, or ``None`` if it must run on its own.

    Run metadata merges thread metadata, so the marker alone does not prove the
    run is takeable. A ``command``, fork checkpoint or webhook would be lost by
    taking it. Kwargs keys are always present, so check values.
    """
    metadata = run.get("metadata")
    kwargs = run.get("kwargs")
    streamed = kwargs.get("input") if isinstance(kwargs, Mapping) else None
    if (
        not (isinstance(metadata, Mapping) and metadata.get(TAKEABLE_METADATA_KEY))
        or run.get("multitask_strategy") != "enqueue"
        or not isinstance(kwargs, Mapping)
        or kwargs.get("command") is not None
        or kwargs.get("webhook") is not None
        or _configurable(kwargs).get("checkpoint_id")
        or _configurable(kwargs).get(_USER_ID_CONFIG_KEY) != principal
        or not (
            isinstance(streamed, Mapping)
            and set(streamed) == {"messages"}
            and isinstance(streamed["messages"], list)
            and streamed["messages"]
        )
    ):
        return None
    run_id = str(run.get("run_id"))
    messages: list[HumanMessage] = []
    for position, message in enumerate(streamed["messages"]):
        if not isinstance(message, Mapping):
            return None
        content = message.get("content")
        if (message.get("role") or message.get("type")) not in {"user", "human"} or not (
            isinstance(content, str) or (isinstance(content, list) and content)
        ):
            return None
        sender_id = message.get("id")
        if isinstance(sender_id, str) and sender_id:
            message_id = sender_id
        else:
            message_id = run_id if position == 0 else f"{run_id}_{position}"
        try:
            messages.append(HumanMessage(content=content, id=message_id))
        except ValidationError:
            return None
    return messages
