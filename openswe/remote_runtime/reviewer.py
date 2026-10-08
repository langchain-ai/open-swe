"""The reviewer's tools and run hooks, served to a reviewer graph running in another deployment.

The remote graph keeps the model loop, its checkpoints and its sandbox, which
the remote deployment creates from the workspace snapshot. Everything that needs
this backend's database, Store or GitHub App runs here: run preparation, the
reviewer tools, the follow-up queue and the check-run settle. Each call runs as
the run the token names.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Final, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from pydantic import BaseModel, JsonValue, TypeAdapter

from openswe.github.thread_token import resolve_thread_github_token
from openswe.middleware import check_message_queue_before_model, settle_review_check_on_exit
from openswe.middleware.check_message_queue import LinearNotifyState
from openswe.prompts import prompt
from openswe.remote_runtime.server import ToolResult, UnknownHookError
from openswe.remote_runtime.tokens import RemoteRun
from openswe.review.diff import compute_diff_line_set, review_diff_path, review_diff_range
from openswe.reviewer import (
    REVIEWER_SUBAGENT_SYSTEM_PROMPT,
    PrepareReviewerRunState,
    ReviewerModelChoice,
    ensure_reviewer_github_token,
    prepare_reviewer_run,
    resolve_reviewer_models,
    reviewer_tools,
)
from openswe.run_config import RunConfig
from openswe.sandboxes.tool_runtime import invoke_tool_node, tool_parameters
from openswe.sandboxes.tool_store import ToolStore
from openswe.store import get_value, put_value

logger = logging.getLogger(__name__)

ASSISTANT_ID: Final = "reviewer"
PREPARE_HOOK: Final = "prepare"
DRAIN_HOOK: Final = "drain"
SETTLE_HOOK: Final = "settle"
# The remote deployment names this MCP server; it exposes each tool as `{server}_{tool}`.
MCP_SERVER_NAME: Final = "openswe"

# Where the remote deployment checks the repository out in its own sandbox.
REMOTE_WORK_DIR: Final = "/workspace"
# Tools the remote deployment serves itself because they act on its sandbox.
REMOTE_SANDBOX_TOOLS: Final = frozenset({"fetch_review_diff"})

# What preparation computed that later tool calls need, keyed by thread: the
# remote graph's state holds it in the in-process reviewer, but tool calls land
# on any replica of this backend.
_RUN_CONTEXT_NAMESPACE: Final = ("remote_runtime", "reviewer_runs")

_json = TypeAdapter(JsonValue)
_json_list = TypeAdapter(list[JsonValue])
_diff_range = TypeAdapter(tuple[str, str, bool] | None)


class ToolSpec(TypedDict):
    name: str
    description: str
    parameters: dict[str, JsonValue]


class SubagentSpec(TypedDict):
    name: str
    description: str
    system_prompt: str


class RuntimeSpec(TypedDict):
    """What the remote project binds at build time, exported to its ``spec.json``.

    The tools this backend serves reach the remote project over MCP instead.
    """

    sandbox_tools: list[ToolSpec]
    subagent: SubagentSpec


class ModelSpec(BaseModel):
    model_id: str
    kwargs: dict[str, JsonValue]


class CheckoutSpec(BaseModel):
    """What the remote deployment checks out in its sandbox, and where it writes the diff."""

    repository: str
    pr_number: int | None
    base_sha: str
    head_sha: str
    repo_dir: str
    diff_base_ref: str
    diff_path: str
    diff_text: str


class RunModels(BaseModel):
    """The models a remote run builds its graph with, chosen when the run is dispatched."""

    model: ModelSpec
    subagent_model: ModelSpec
    use_gateway: bool


class PreparedRun(BaseModel):
    """A prepared run as the remote graph receives it; never carries credentials."""

    system_prompt: str
    work_dir: str
    checkout: CheckoutSpec | None


class _RunContext(BaseModel):
    invocation_id: str | None
    work_dir: str | None
    diff_text: str
    review_approval_policy: str | None


class _NodeState(BaseModel):
    done: bool = False


def _tool_specs() -> dict[str, ToolSpec]:
    return {
        name: {"name": name, "description": tool.description, "parameters": tool_parameters(tool)}
        for name, tool in ToolNode(reviewer_tools()).tools_by_name.items()
    }


def _tool_node() -> ToolNode:
    tools = ToolNode(reviewer_tools()).tools_by_name
    return ToolNode([tool for name, tool in tools.items() if name not in REMOTE_SANDBOX_TOOLS])


def served_tools() -> list[ToolSpec]:
    """The tools this backend serves to the remote deployment over MCP."""
    return [spec for name, spec in _tool_specs().items() if name not in REMOTE_SANDBOX_TOOLS]


def runtime_spec() -> RuntimeSpec:
    return {
        "sandbox_tools": [
            spec for name, spec in _tool_specs().items() if name in REMOTE_SANDBOX_TOOLS
        ],
        "subagent": {
            "name": "reviewer",
            "description": prompt("reviewer/subagent-description"),
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


async def run_models(cfg: RunConfig) -> RunModels:
    models = await resolve_reviewer_models(cfg)
    return RunModels(
        model=_model_spec(models.main),
        subagent_model=_model_spec(models.subagent),
        use_gateway=models.use_gateway,
    )


def _checkout_spec(
    cfg: RunConfig, diff_text: str, diff_range: tuple[str, str, bool] | None
) -> CheckoutSpec | None:
    if cfg.repo is None or not cfg.head_sha:
        return None
    repo_dir = f"{REMOTE_WORK_DIR}/{cfg.repo.name}"
    base_ref, head_ref, merge_base = diff_range or review_diff_range(
        base_sha=cfg.base_sha or "",
        head_sha=cfg.head_sha,
        last_reviewed_sha=cfg.last_reviewed_sha or "",
        re_review=bool(cfg.re_review),
    )
    return CheckoutSpec(
        repository=cfg.repo.full_name,
        pr_number=cfg.pr_number,
        base_sha=cfg.base_sha or "",
        head_sha=cfg.head_sha,
        repo_dir=repo_dir,
        diff_base_ref=base_ref,
        diff_path=review_diff_path(repo_dir, base_ref, head_ref, merge_base),
        diff_text=diff_text,
    )


async def prepare_run(run: RemoteRun) -> PreparedRun:
    config = _run_config(run)
    cfg = RunConfig.from_config(config)
    prepared = await _in_graph_context(
        config,
        lambda runtime: prepare_reviewer_run(
            run.thread_id, config, runtime, remote_work_dir=REMOTE_WORK_DIR
        ),
    )
    diff_text = prepared.get("diff_text")
    approval_policy = prepared.get("review_approval_policy")
    system_prompt = prepared.get("rendered_system_prompt")
    if not isinstance(system_prompt, str):
        raise RuntimeError("Reviewer preparation rendered no system prompt")
    context = _RunContext(
        invocation_id=cfg.invocation_id,
        work_dir=REMOTE_WORK_DIR,
        diff_text=diff_text if isinstance(diff_text, str) else "",
        review_approval_policy=approval_policy if isinstance(approval_policy, str) else None,
    )
    await put_value(_RUN_CONTEXT_NAMESPACE, run.thread_id, context.model_dump())
    return PreparedRun(
        system_prompt=f"{system_prompt}\n\n"
        + prompt("reviewer/remote-tool-names", prefix=f"{MCP_SERVER_NAME}_"),
        work_dir=REMOTE_WORK_DIR,
        checkout=_checkout_spec(
            cfg, context.diff_text, _diff_range.validate_python(prepared.get("diff_range"))
        ),
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


async def call_tool(run: RemoteRun, name: str, arguments: Mapping[str, JsonValue]) -> ToolResult:
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
    # ty does not yet treat TypedDict classes as LangGraph's TypedDictLike state bound.
    return ToolResult.model_validate(
        await invoke_tool_node(
            node,
            PrepareReviewerRunState,  # ty: ignore[invalid-argument-type]
            await _tool_state(run),
            config,
            name,
            arguments,
        )
    )


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


async def run_hook(run: RemoteRun, hook: str) -> JsonValue:
    """Answer one run hook the remote graph calls around its model loop."""
    if hook == PREPARE_HOOK:
        return _json.validate_python((await prepare_run(run)).model_dump())
    if hook == DRAIN_HOOK:
        return await drain_message_queue(run)
    if hook == SETTLE_HOOK:
        return await settle_review_check(run)
    raise UnknownHookError(hook)
