"""The reviewer's tools and run hooks, served to a reviewer graph running in another deployment.

The remote graph keeps only the model loop and its checkpoints. Everything that
needs this backend's database, Store, GitHub App or sandbox lifecycle runs here:
run preparation, every reviewer tool, the GitHub proxy refresh, the follow-up
queue and the check-run settle. Each call runs as the run the token names.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Final, Literal, TypedDict

from deepagents.backends.protocol import SandboxBackendProtocol
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from pydantic import BaseModel, JsonValue, TypeAdapter

from openswe.config import ENV
from openswe.github.proxy import maybe_refresh_proxy_token
from openswe.github.thread_token import resolve_thread_github_token
from openswe.middleware import check_message_queue_before_model, settle_review_check_on_exit
from openswe.middleware.check_message_queue import LinearNotifyState
from openswe.prompts import load_prompt
from openswe.remote_runtime.tokens import RemoteRun
from openswe.review.diff import compute_diff_line_set
from openswe.reviewer import (
    REVIEWER_SUBAGENT_SYSTEM_PROMPT,
    PrepareReviewerRunState,
    ReviewerModelChoice,
    ensure_reviewer_github_token,
    ensure_reviewer_sandbox_for_thread,
    prepare_reviewer_run,
    resolve_reviewer_models,
    reviewer_tools,
)
from openswe.run_config import RunConfig
from openswe.runtime import get_cached_sandbox_backend
from openswe.sandboxes.state import get_sandbox_id_from_metadata
from openswe.sandboxes.tool_runtime import invoke_tool_node, tool_parameters
from openswe.sandboxes.tool_store import ToolStore
from openswe.store import get_value, put_value

logger = logging.getLogger(__name__)

ASSISTANT_ID: Final = "reviewer"
PREPARE_HOOK: Final = "runtime__prepare_run"
REFRESH_HOOK: Final = "runtime__refresh_sandbox_credentials"
DRAIN_HOOK: Final = "runtime__drain_message_queue"
SETTLE_HOOK: Final = "runtime__settle_review_check"
RUNTIME_HOOKS: Final = frozenset({PREPARE_HOOK, REFRESH_HOOK, DRAIN_HOOK, SETTLE_HOOK})

# What preparation computed that later tool calls need, keyed by thread: the
# remote graph's state holds it in the in-process reviewer, but tool calls land
# on any replica of this backend.
_RUN_CONTEXT_NAMESPACE: Final = ("remote_runtime", "reviewer_runs")

_json = TypeAdapter(JsonValue)
_json_list = TypeAdapter(list[JsonValue])


class ToolSpec(TypedDict):
    name: str
    description: str
    parameters: dict[str, JsonValue]


class SubagentSpec(TypedDict):
    name: str
    description: str
    system_prompt: str


class RuntimeSpec(TypedDict):
    """What the remote project binds at build time, exported to its ``spec.json``."""

    tools: list[ToolSpec]
    subagent: SubagentSpec


class ModelSpec(BaseModel):
    model_id: str
    kwargs: dict[str, JsonValue]


class SandboxSpec(BaseModel):
    provider: Literal["langsmith"]
    sandbox_id: str


class PreparedRun(BaseModel):
    """A prepared run as the remote graph receives it; never carries credentials."""

    system_prompt: str
    work_dir: str | None
    sandbox: SandboxSpec
    model: ModelSpec
    subagent_model: ModelSpec
    use_gateway: bool


class _RunContext(BaseModel):
    invocation_id: str | None
    work_dir: str | None
    diff_text: str
    review_approval_policy: str | None


class _NodeState(BaseModel):
    done: bool = False


def _tool_node() -> ToolNode:
    return ToolNode(reviewer_tools())


def runtime_spec() -> RuntimeSpec:
    tools: list[ToolSpec] = [
        {"name": name, "description": tool.description, "parameters": tool_parameters(tool)}
        for name, tool in _tool_node().tools_by_name.items()
    ]
    return {
        "tools": tools,
        "subagent": {
            "name": "reviewer",
            "description": load_prompt("reviewer/subagent-description.md"),
            "system_prompt": REVIEWER_SUBAGENT_SYSTEM_PROMPT,
        },
    }


def model_tool_names() -> frozenset[str]:
    return frozenset(_tool_node().tools_by_name)


def _run_config(run: RemoteRun) -> RunnableConfig:
    return {
        "configurable": {
            **run.configurable,
            "thread_id": run.thread_id,
            "__is_for_execution__": True,
        }
    }


async def _in_graph_context[T](
    config: RunnableConfig, work: Callable[[Runtime], Awaitable[T]]
) -> T:
    """Run ``work`` inside a one-node graph so ``get_config`` and ``get_store`` resolve."""
    results: list[T] = []

    async def node(state: _NodeState, runtime: Runtime) -> dict[str, bool]:  # noqa: ARG001
        results.append(await work(runtime))
        return {"done": True}

    builder = StateGraph(_NodeState)
    builder.add_node("work", node)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    await builder.compile(store=ToolStore()).ainvoke(_NodeState(), config)
    return results[0]


def _model_spec(choice: ReviewerModelChoice) -> ModelSpec:
    return ModelSpec(model_id=choice.model_id, kwargs=_json.validate_python(dict(choice.kwargs)))


async def prepare_run(run: RemoteRun) -> PreparedRun:
    if ENV.SANDBOX_TYPE.get() != "langsmith":
        raise RuntimeError("Remote reviewer runs require the langsmith sandbox provider")
    config = _run_config(run)
    cfg = RunConfig.from_config(config)
    prepared = await _in_graph_context(
        config, lambda runtime: prepare_reviewer_run(run.thread_id, config, runtime)
    )
    work_dir = prepared.get("work_dir")
    diff_text = prepared.get("diff_text")
    approval_policy = prepared.get("review_approval_policy")
    system_prompt = prepared.get("rendered_system_prompt")
    if not isinstance(system_prompt, str):
        raise RuntimeError("Reviewer preparation rendered no system prompt")
    context = _RunContext(
        invocation_id=cfg.invocation_id,
        work_dir=work_dir if isinstance(work_dir, str) else None,
        diff_text=diff_text if isinstance(diff_text, str) else "",
        review_approval_policy=approval_policy if isinstance(approval_policy, str) else None,
    )
    await put_value(_RUN_CONTEXT_NAMESPACE, run.thread_id, context.model_dump())
    sandbox_id = await get_sandbox_id_from_metadata(run.thread_id)
    if not sandbox_id:
        raise RuntimeError("Reviewer preparation left the thread without a sandbox")
    models = await resolve_reviewer_models(cfg)
    return PreparedRun(
        system_prompt=system_prompt,
        work_dir=context.work_dir,
        sandbox=SandboxSpec(provider="langsmith", sandbox_id=sandbox_id),
        model=_model_spec(models.main),
        subagent_model=_model_spec(models.subagent),
        use_gateway=models.use_gateway,
    )


async def _tool_state(run: RemoteRun) -> PrepareReviewerRunState:
    stored = await get_value(_RUN_CONTEXT_NAMESPACE, run.thread_id)
    if stored is None:
        return {"messages": []}
    context = _RunContext.model_validate(stored)
    if context.invocation_id != RunConfig.parse(run.configurable).invocation_id:
        # Left by an earlier run on this thread; its diff is not this run's.
        return {"messages": []}
    state: PrepareReviewerRunState = {
        "messages": [],
        "review_approval_policy": context.review_approval_policy,
    }
    if context.diff_text:
        state["diff_text"] = context.diff_text
        state["diff_line_set"] = compute_diff_line_set(context.diff_text)
    if context.work_dir:
        state["work_dir"] = context.work_dir
    return state


async def call_tool(
    run: RemoteRun, name: str, arguments: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    node = _tool_node()
    tool = node.tools_by_name.get(name)
    if tool is None:
        raise ValueError(f"Unknown reviewer tool: {name}")
    properties = tool_parameters(tool).get("properties", {})
    if not isinstance(properties, dict) or set(arguments) - properties.keys():
        raise ValueError(f"Unexpected arguments for {name}")
    config = _run_config(run)
    cfg = RunConfig.from_config(config)
    if await resolve_thread_github_token(config) is None:
        # This replica did not prepare the run, so it holds no token for the thread yet.
        await ensure_reviewer_github_token(run.thread_id, cfg)

    async def reconnect() -> SandboxBackendProtocol:
        backend, _ = await ensure_reviewer_sandbox_for_thread(run.thread_id, cfg)
        return backend

    get_cached_sandbox_backend(run.thread_id, reconnect=reconnect)
    # ty does not yet treat TypedDict classes as LangGraph's TypedDictLike state bound.
    return await invoke_tool_node(
        node,
        PrepareReviewerRunState,  # ty: ignore[invalid-argument-type]
        await _tool_state(run),
        config,
        name,
        arguments,
    )


async def refresh_sandbox_credentials(run: RemoteRun) -> dict[str, JsonValue]:
    return {"refreshed": await maybe_refresh_proxy_token(run.thread_id)}


async def drain_message_queue(run: RemoteRun) -> dict[str, JsonValue]:
    async def drain(runtime: Runtime) -> dict[str, object] | None:
        return await check_message_queue_before_model.abefore_model(
            LinearNotifyState(messages=[], linear_messages_sent_count=0), runtime
        )

    update = await _in_graph_context(_run_config(run), drain)
    messages = (update or {}).get("messages")
    return {"messages": _json_list.validate_python(messages if isinstance(messages, list) else [])}


async def settle_review_check(run: RemoteRun) -> dict[str, JsonValue]:
    async def settle(runtime: Runtime) -> None:
        await settle_review_check_on_exit.aafter_agent({"messages": []}, runtime)

    await _in_graph_context(_run_config(run), settle)
    return {"settled": True}


async def call(
    run: RemoteRun, name: str, arguments: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    """Answer one tool server call for a reviewer run: a run hook or a model's tool call."""
    if name == PREPARE_HOOK:
        return _json.validate_python((await prepare_run(run)).model_dump())
    if name == REFRESH_HOOK:
        return await refresh_sandbox_credentials(run)
    if name == DRAIN_HOOK:
        return await drain_message_queue(run)
    if name == SETTLE_HOOK:
        return await settle_review_check(run)
    return await call_tool(run, name, arguments)
