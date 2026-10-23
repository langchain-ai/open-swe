"""Run one coding task and consume its graph-produced patch artifact."""

import hashlib
import logging
import os
import threading
import time
from collections.abc import Mapping
from typing import Literal

from langgraph_sdk import get_client
from pydantic import BaseModel, Field, ValidationError

from agent.evaluation import AgentEvaluationArtifact, AgentEvaluationConfig, CompactionConfig
from agent.input_messages import InputMessageContext, SystemIdentity, build_run_input
from agent.prompts import render_prompt
from agent.sandboxes.environment import ExecutionEnvironment
from evals.agent.models import CompactionTelemetry, EvalInputs, FailureStage, TargetResult

logger = logging.getLogger(__name__)
DEFAULT_AGENT_ASSISTANT_ID = "agent"
DEFAULT_LANGGRAPH_URL = "http://localhost:2024"
_THREAD_IDS: set[str] = set()
_THREAD_IDS_LOCK = threading.Lock()
_COMPLETED = 0
_COMPLETED_LOCK = threading.Lock()


def get_completed_count() -> int:
    with _COMPLETED_LOCK:
        return _COMPLETED


def drain_thread_ids() -> set[str]:
    with _THREAD_IDS_LOCK:
        snapshot = set(_THREAD_IDS)
        _THREAD_IDS.clear()
    return snapshot


def get_langgraph_url() -> str:
    return os.getenv("LANGGRAPH_URL", DEFAULT_LANGGRAPH_URL)


def get_agent_assistant_id() -> str:
    return os.getenv("AGENT_ASSISTANT_ID", DEFAULT_AGENT_ASSISTANT_ID)


def get_agent_model_id() -> str | None:
    return os.getenv("AGENT_EVAL_MODEL_ID") or None


def get_agent_reasoning_effort() -> str | None:
    return os.getenv("AGENT_EVAL_REASONING_EFFORT") or None


def get_eval_run_source() -> Literal["eval", "desktop"]:
    return "desktop" if os.getenv("AGENT_EVAL_RUN_SOURCE") == "desktop" else "eval"


def get_local_project_dir() -> str | None:
    value = os.getenv("AGENT_EVAL_LOCAL_PROJECT_DIR", "").strip()
    return value or None


def get_compaction_config() -> CompactionConfig | None:
    """Compaction experiment arm, or ``None`` when no override is configured."""
    raw: dict[str, object] = {}
    for env_name, field in (
        ("AGENT_EVAL_COMPACTION_TRIGGER_TOKENS", "trigger_tokens"),
        ("AGENT_EVAL_COMPACTION_TRIGGER_MESSAGES", "trigger_messages"),
    ):
        value = os.getenv(env_name, "").strip()
        if value:
            raw[field] = int(value)
    fraction = os.getenv("AGENT_EVAL_COMPACTION_TRIGGER_FRACTION", "").strip()
    if fraction:
        raw["trigger_fraction"] = float(fraction)
    prompt = os.getenv("AGENT_EVAL_COMPACTION_SUMMARY_PROMPT", "").strip()
    if prompt:
        raw["summary_prompt"] = prompt
    return CompactionConfig.model_validate(raw) if raw else None


def _eval_context() -> InputMessageContext:
    return {"sender_id": "system:agent-eval", "surface": "eval", "kind": "system"}


def _eval_systems() -> list[SystemIdentity]:
    return [{"id": "system:agent-eval", "display_name": "Agent evaluation", "platform": "open-swe"}]


def _build_configurable(
    inputs: Mapping[str, object], *, checkout_dir: str = "eval-0000000000000000"
) -> dict[str, object]:
    repo = inputs.get("repo", "")
    owner, _, name = repo.partition("/") if isinstance(repo, str) else ("", "", "")
    options = AgentEvaluationConfig.model_validate(
        {
            "base_commit": inputs.get("base_commit"),
            "checkout_dir": checkout_dir,
            "compaction": get_compaction_config(),
        }
    )
    configurable: dict[str, object] = {
        "__is_for_execution__": True,
        "eval": True,
        "agent_eval": options.model_dump(),
        "repo": {"owner": owner, "name": name},
        "source": get_eval_run_source(),
    }
    if configurable["source"] == "desktop":
        project_dir = get_local_project_dir()
        if not project_dir:
            raise ValueError("run_source 'desktop' requires AGENT_EVAL_LOCAL_PROJECT_DIR")
        configurable["local_project_path"] = project_dir
    if model_id := get_agent_model_id():
        configurable["agent_model_id"] = model_id
    if effort := get_agent_reasoning_effort():
        configurable["agent_effort"] = effort
    return configurable


def _solve_prompt(inputs: EvalInputs, checkout_dir: str) -> str:
    return render_prompt(
        "evals/agent-task.md",
        **inputs.model_dump(exclude={"clone_url"}),
        clone_url=inputs.clone_url or f"https://github.com/{inputs.repo}.git",
        checkout_dir=checkout_dir,
    )


class _Usage(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class _Message(BaseModel):
    type: str
    usage_metadata: _Usage | None = None


class _OffloadState(BaseModel):
    count: int = Field(default=0, ge=0)
    total_messages_summarized: int = Field(default=0, ge=0)
    total_summarized_tokens: int = Field(default=0, ge=0)
    total_summary_tokens: int = Field(default=0, ge=0)
    total_duration_ms: int = Field(default=0, ge=0)
    overflow_count: int = Field(default=0, ge=0)


class _GraphResult(BaseModel):
    messages: list[_Message] = Field(default_factory=list)
    agent_eval_result: AgentEvaluationArtifact | None = None
    execution_environment: ExecutionEnvironment | None = None
    conversation_offloading: _OffloadState | None = None


async def solve_task(inputs: dict[str, object]) -> dict[str, object]:
    """LangSmith target; execution failures remain measurable experiment rows."""
    global _COMPLETED
    started = time.monotonic()
    output = TargetResult(
        instance_id=str(inputs.get("instance_id", "")), repo=str(inputs.get("repo", ""))
    )
    try:
        instance = EvalInputs.model_validate(inputs)
        output.instance_id = instance.instance_id
        output.repo = instance.repo
        client = get_client(url=get_langgraph_url())
        thread = await client.threads.create()
        output.thread_id = thread["thread_id"]
        with _THREAD_IDS_LOCK:
            _THREAD_IDS.add(output.thread_id)
        checkout_dir = "eval-" + hashlib.sha256(output.thread_id.encode()).hexdigest()[:16]
        configurable = _build_configurable(inputs, checkout_dir=checkout_dir)
        raw = await client.runs.wait(
            output.thread_id,
            assistant_id=get_agent_assistant_id(),
            input=build_run_input(
                _solve_prompt(instance, checkout_dir), _eval_context(), systems=_eval_systems()
            ),
            config={"configurable": configurable},
        )
        output.failure_stage = FailureStage.PATCH_COLLECTION
        result = _GraphResult.model_validate(raw)
        output.execution_environment = result.execution_environment
        if result.conversation_offloading is not None:
            offloading = result.conversation_offloading
            output.compaction = CompactionTelemetry(
                count=offloading.count,
                messages_summarized=offloading.total_messages_summarized,
                summarized_tokens=offloading.total_summarized_tokens,
                summary_tokens=offloading.total_summary_tokens,
                duration_ms=offloading.total_duration_ms,
                overflow_count=offloading.overflow_count,
            )
        messages = [message for message in result.messages if message.type == "ai"]
        output.steps = len(messages)
        usages = [
            message.usage_metadata for message in messages if message.usage_metadata is not None
        ]
        output.usage_complete = bool(messages) and len(usages) == len(messages)
        if usages:
            output.input_tokens = sum(usage.input_tokens for usage in usages)
            output.output_tokens = sum(usage.output_tokens for usage in usages)
        artifact = result.agent_eval_result
        if artifact is None:
            output.error = "The graph returned no agent_eval_result; upgrade the agent server"
        elif artifact.status == "collection_error":
            output.error = artifact.error or "The graph could not collect the patch"
        else:
            output.patch = artifact.patch
            output.failure_stage = FailureStage.NONE
    except Exception as exc:
        logger.exception("Agent eval example failed", extra={"instance_id": output.instance_id})
        if output.failure_stage == FailureStage.NONE:
            output.failure_stage = (
                FailureStage.INVALID_INSTANCE
                if isinstance(exc, ValidationError) and not output.thread_id
                else FailureStage.AGENT_EXECUTION
            )
        output.error = f"{type(exc).__name__}: {exc}"
    finally:
        output.wall_clock_seconds = round(time.monotonic() - started, 3)
        with _COMPLETED_LOCK:
            _COMPLETED += 1
    logger.info(
        "Finished agent eval example",
        extra={
            "instance_id": output.instance_id,
            "thread_id": output.thread_id,
            "failure_stage": output.failure_stage,
            "steps": output.steps,
            "wall_clock_seconds": output.wall_clock_seconds,
        },
    )
    return output.model_dump(mode="json")
