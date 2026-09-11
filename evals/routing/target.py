"""Target for the routing eval: run one dashboard-style thread up to its routing decision.

Starts a real run against a running ``agent`` graph with the dashboard's defaults
(``model_selection: auto``), streams state values, and cancels the run as soon
as the model calls ``exit_pre_routed_mode``. What happened before that call is
the measurement: the route, the title, and how much exploration it took.
"""

import logging
import os
import threading
import time
import uuid
from typing import Any, Literal

from langgraph_sdk import get_client
from pydantic import BaseModel, ConfigDict, Field

from agent.input_messages import build_run_input

logger = logging.getLogger(__name__)

DEFAULT_ASSISTANT_ID = "agent"
DEFAULT_LANGGRAPH_URL = "http://localhost:2024"
DEFAULT_LOGIN = "routing-eval"
EXIT_TOOL = "exit_pre_routed_mode"
REJECTION_MARKER = "unavailable in pre-routed mode"

_THREAD_IDS: set[str] = set()
_LOCK = threading.Lock()


def drain_thread_ids() -> set[str]:
    with _LOCK:
        snapshot = set(_THREAD_IDS)
        _THREAD_IDS.clear()
    return snapshot


def get_langgraph_url() -> str:
    return os.getenv("LANGGRAPH_URL", DEFAULT_LANGGRAPH_URL)


def get_assistant_id() -> str:
    return os.getenv("ROUTING_EVAL_ASSISTANT_ID", DEFAULT_ASSISTANT_ID)


def get_login() -> str:
    return os.getenv("ROUTING_EVAL_GITHUB_LOGIN", DEFAULT_LOGIN)


def get_timeout_seconds() -> float:
    return float(os.getenv("ROUTING_EVAL_TIMEOUT_SECONDS", "600"))


class _ToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class _Usage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    input_tokens: int = 0
    output_tokens: int = 0


class _Message(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str
    content: Any = ""
    tool_calls: list[_ToolCall] = Field(default_factory=list)
    usage_metadata: _Usage | None = None
    status: str | None = None

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            return " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in self.content
            )
        return str(self.content)


class RoutingOutcome(BaseModel):
    route: Literal["fast", "balanced", "performance"] | None
    title: str | None
    exited: bool
    finished_without_exit: bool
    timed_out: bool
    model_calls_before_exit: int
    tool_calls_before_exit: int
    tools_used: list[str]
    rejected_tool_calls: int
    input_tokens: int
    output_tokens: int
    seconds_to_decision: float
    final_text: str
    thread_id: str


def _thread_metadata(thread_id: str, prompt: str, repo: str, login: str) -> dict[str, Any]:
    owner, _, name = repo.partition("/")
    now_ms = int(time.time() * 1000)
    title = prompt[:80]
    return {
        "source": "dashboard",
        "origin": "dashboard",
        "owner_type": "user",
        "owner_login": login,
        "visibility": "private",
        "thread_category": "interactive",
        "trigger_kind": "user",
        "participant_logins": {login: True},
        "title": title,
        "title_seed": title,
        "base_branch": "main",
        "model_selection": "auto",
        "repo_owner": owner,
        "repo_name": name,
        "routing_eval": True,
        "created_at_ms": now_ms,
        "updated_at_ms": now_ms,
        "thread_id": thread_id,
    }


def _configurable(thread_id: str, repo: str, login: str) -> dict[str, Any]:
    owner, _, name = repo.partition("/")
    return {
        "__is_for_execution__": True,
        "eval": True,
        "thread_id": thread_id,
        "source": "dashboard",
        "github_login": login,
        "user_email": f"{login}@example.com",
        "repo": {"owner": owner, "name": name},
        "model_selection": "auto",
    }


def _messages(values: Any) -> list[_Message]:
    if not isinstance(values, dict):
        return []
    raw = values.get("messages")
    if not isinstance(raw, list):
        return []
    return [_Message.model_validate(m) for m in raw if isinstance(m, dict)]


def _exit_call(messages: list[_Message]) -> tuple[int, _ToolCall] | None:
    for index, message in enumerate(messages):
        if message.type != "ai":
            continue
        for call in message.tool_calls:
            if call.name == EXIT_TOOL:
                return index, call
    return None


def _outcome(
    messages: list[_Message],
    *,
    thread_id: str,
    started: float,
    decided_at: float | None,
    timed_out: bool,
) -> RoutingOutcome:
    exit_position = _exit_call(messages)
    cutoff = exit_position[0] + 1 if exit_position else len(messages)
    considered = messages[:cutoff]
    ai_messages = [m for m in considered if m.type == "ai"]
    tool_names = [call.name for m in ai_messages for call in m.tool_calls if call.name != EXIT_TOOL]
    rejected = sum(
        1
        for m in considered
        if m.type == "tool" and m.status == "error" and REJECTION_MARKER in m.text()
    )
    usage_in = sum(m.usage_metadata.input_tokens for m in ai_messages if m.usage_metadata)
    usage_out = sum(m.usage_metadata.output_tokens for m in ai_messages if m.usage_metadata)
    final_text = next((m.text() for m in reversed(messages) if m.type == "ai"), "")
    call = exit_position[1] if exit_position else None
    route = call.args.get("model_route") if call else None
    title = call.args.get("title") if call else None
    return RoutingOutcome(
        route=route if route in {"fast", "balanced", "performance"} else None,
        title=title if isinstance(title, str) else None,
        exited=call is not None,
        finished_without_exit=call is None and not timed_out,
        timed_out=timed_out,
        model_calls_before_exit=len(ai_messages) - (1 if call else 0),
        tool_calls_before_exit=len(tool_names),
        tools_used=sorted(set(tool_names)),
        rejected_tool_calls=rejected,
        input_tokens=usage_in,
        output_tokens=usage_out,
        seconds_to_decision=round((decided_at or time.monotonic()) - started, 1),
        final_text=final_text[:2000],
        thread_id=thread_id,
    )


async def route_task(inputs: dict[str, Any]) -> dict[str, Any]:
    """LangSmith target: observe the routing decision for one task."""
    prompt = str(inputs["prompt"])
    repo = str(inputs.get("repo") or "langchain-ai/open-swe")
    login = get_login()
    client = get_client(url=get_langgraph_url())
    thread_id = str(uuid.uuid4())
    await client.threads.create(
        thread_id=thread_id, metadata=_thread_metadata(thread_id, prompt, repo, login)
    )
    with _LOCK:
        _THREAD_IDS.add(thread_id)

    run_input = build_run_input(
        prompt,
        {"sender_id": f"github:{login}", "surface": "web", "kind": "human"},
        people=[{"id": f"github:{login}", "platform": "github", "github_login": login}],
    )
    started = time.monotonic()
    deadline = started + get_timeout_seconds()
    run_id: str | None = None
    latest: list[_Message] = []
    decided_at: float | None = None
    timed_out = False
    stream = client.runs.stream(
        thread_id,
        get_assistant_id(),
        input=run_input,
        config={"configurable": _configurable(thread_id, repo, login)},
        stream_mode="values",
    )
    async for part in stream:
        if part.event == "metadata" and isinstance(part.data, dict):
            run_id = part.data.get("run_id") or run_id
            continue
        if part.event == "values":
            latest = _messages(part.data)
            if _exit_call(latest) is not None:
                decided_at = time.monotonic()
                break
        if time.monotonic() > deadline:
            timed_out = True
            break
    if run_id and (decided_at is not None or timed_out):
        try:
            await client.runs.cancel(thread_id, run_id, action="interrupt")
        except Exception:  # noqa: BLE001
            logger.warning("Could not cancel eval run", extra={"thread": thread_id, "run": run_id})
    outcome = _outcome(
        latest, thread_id=thread_id, started=started, decided_at=decided_at, timed_out=timed_out
    )
    logger.info(
        "Routing eval example finished",
        extra={
            "task": inputs.get("task_id"),
            "route": outcome.route,
            "tool_calls": outcome.tool_calls_before_exit,
            "seconds": outcome.seconds_to_decision,
        },
    )
    return outcome.model_dump()
