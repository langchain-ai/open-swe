"""Review scout graph.

Plans a pull request's shared walkthrough in the background: places every
changed line in a chunk or in Other, and records a summary of the human input
behind the change. Runs on its own thread per PR. On a new head it only places
the lines the carried-over plan does not already cover, and when there are
none it finishes without a model call.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, NotRequired, cast

from deepagents import create_deep_agent
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRetryMiddleware
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    hook_config,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph.runtime import Runtime

from openswe.dashboard.options import gate_fable_model
from openswe.dashboard.workspace_settings_cache import cached_workspace_settings
from openswe.github.app import get_github_app_installation_token_with_expiry
from openswe.github.pull_request_key import PullRequestKey
from openswe.github.thread_token import cache_github_token_for_thread
from openswe.middleware import (
    BasePrepareRunMiddleware,
    ModelCallTimeoutMiddleware,
    ModelErrorMiddleware,
    RepairOrphanedToolCallsMiddleware,
    SanitizeFireworksMessagesMiddleware,
    SanitizeOpenAIResponsesMiddleware,
    SanitizeThinkingBlocksMiddleware,
    StableToolResultOrderMiddleware,
    ToolErrorMiddleware,
)
from openswe.middleware.prepare_run import PrepareRunState
from openswe.middleware.trace import OpenSWEMiddleware
from openswe.prompts import apply_tool_descriptions, prompt
from openswe.review.author_guidance import SteeringHistory
from openswe.run_config import RunConfig
from openswe.runtime import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_RECURSION_LIMIT,
    bindable_config,
    ensure_sandbox_for_thread,
    get_cached_sandbox_backend,
    graph_loaded_for_execution,
)
from openswe.sandboxes.repo_prep import prepare_review_repo
from openswe.tools.plan_walkthrough import (
    walkthrough_describe_other,
    walkthrough_move_to_other,
    walkthrough_plan_chunk,
)
from openswe.tools.record_human_input import record_human_input
from openswe.ui_invalidations import Topic
from openswe.utils.deferred_model import make_deferred_error_model
from openswe.utils.model import DEFAULT_LLM_REASONING, make_model, provider_model_kwargs
from openswe.walkthrough.checkout import CheckoutError, PinnedCheckout
from openswe.walkthrough.planner import PlannerUnavailableError, PlanWorkspace
from openswe.walkthrough.record import PlanMovedError, Walkthrough

logger = logging.getLogger(__name__)

SCOUT_MODEL_CALL_LIMIT = 150
_CLOSING_TITLE_TAG_RE = re.compile(r"</\s*pr_title\s*>", re.IGNORECASE)
_HUMAN_INPUT_TOOL = record_human_input.__name__


class ReviewScoutState(PrepareRunState):
    scout_nothing_to_plan: NotRequired[bool]
    human_input_summary: NotRequired[str]
    has_human_input: NotRequired[bool]


async def _workspace(backend: SandboxBackendProtocol, cfg: RunConfig) -> PlanWorkspace:
    if cfg.repo is None or cfg.pr_number is None:
        raise PlannerUnavailableError("review scout run is missing its pull request")
    return await PlanWorkspace.locate(
        backend, owner=cfg.repo.owner, repo=cfg.repo.name, number=cfg.pr_number
    )


async def _ensure_scout_sandbox(thread_id: str, cfg: RunConfig) -> SandboxBackendProtocol:
    repo_name = cfg.repo.name if cfg.repo else ""
    repositories = [repo_name] if repo_name else None
    token, expires_at = await get_github_app_installation_token_with_expiry(
        repositories=repositories
    )
    if not token:
        raise RuntimeError(
            f"GitHub App installation token unavailable for scout thread {thread_id}"
        )
    cache_github_token_for_thread(
        thread_id, token, expires_at=expires_at, is_bot_token=True, repositories=repositories
    )
    # Like the reviewer's, a scout sandbox holds only a checkout every run re-derives.
    return await ensure_sandbox_for_thread(
        thread_id,
        workspace_slug=cfg.workspace_slug,
        github_proxy_repositories=[cfg.repo.full_name] if cfg.repo else [],
        allow_replacement=True,
    )


class PrepareReviewScoutRunMiddleware(BasePrepareRunMiddleware):
    state_schema = ReviewScoutState

    def __init__(self, *, thread_id: str, config: RunnableConfig) -> None:
        self._thread_id = thread_id
        self._config = config

    def _prepare_config_fingerprint(self) -> object:
        cfg = RunConfig.from_config(self._config)
        return {
            "invocation_id": cfg.invocation_id,
            "thread_id": self._thread_id,
            "repo": cfg.repo.model_dump() if cfg.repo else None,
            "pr_number": cfg.pr_number,
            "base_sha": cfg.base_sha,
            "head_sha": cfg.head_sha,
        }

    async def _prepare(self, state: PrepareRunState, runtime: Runtime) -> dict[str, Any]:  # noqa: ARG002
        cfg = RunConfig.from_config(self._config)
        if cfg.repo is None or cfg.pr_number is None or not cfg.base_sha or not cfg.head_sha:
            raise RuntimeError("review scout run is missing its pull request")
        backend = await _ensure_scout_sandbox(self._thread_id, cfg)
        checkout = await PinnedCheckout.locate(backend, cfg.repo.name)
        work_dir = checkout.repo_dir.rpartition("/")[0]
        ready = await prepare_review_repo(
            backend,
            work_dir=work_dir,
            repo_owner=cfg.repo.owner,
            repo_name=cfg.repo.name,
            head_sha=cfg.head_sha,
            pr_number=cfg.pr_number,
            base_sha=cfg.base_sha,
        )
        if not ready:
            raise RuntimeError("review scout could not check out the pull request")
        await checkout.pin(
            full_name=cfg.repo.full_name,
            pr_number=cfg.pr_number,
            base_sha=cfg.base_sha,
            head_sha=cfg.head_sha,
        )
        try:
            workspace = await _workspace(backend, cfg)
        except PlanMovedError:
            # A newer head's scout owns the plan now.
            logger.info(
                "Review scout head is stale; nothing to plan",
                extra={"pr_number": cfg.pr_number, "scout_head_sha": cfg.head_sha},
            )
            workspace = None
        if workspace is None or workspace.complete:
            return {
                "work_dir": work_dir,
                "rendered_system_prompt": "",
                "scout_nothing_to_plan": True,
                "has_human_input": False,
            }
        system_prompt = prompt(
            "review-scout/main",
            pr_number=cfg.pr_number,
            repo_full_name=cfg.repo.full_name,
            pr_title=_CLOSING_TITLE_TAG_RE.sub("</pr_title_>", cfg.pr_title or ""),
            repo_dir=checkout.repo_dir,
            carried=bool(workspace.plan.chunks),
            plan=workspace.status().model_dump(),
        )
        history = await SteeringHistory.load(cfg.repo.owner, cfg.repo.name, cfg.pr_number)
        if history is not None:
            human_input = prompt("review-scout/human-input", messages=history.messages_block())
            system_prompt = f"{system_prompt}\n\n{human_input}"
        return {
            "work_dir": work_dir,
            "rendered_system_prompt": system_prompt,
            "scout_nothing_to_plan": False,
            "human_input_summary": "",
            "has_human_input": history is not None,
        }

    @hook_config(can_jump_to=["end"])
    async def abefore_model(
        self,
        state: ReviewScoutState,
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        return {"jump_to": "end"} if state.get("scout_nothing_to_plan") else None

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        # Without messages from the people who asked for the PR, the model would summarize its description instead.
        if not request.state.get("has_human_input"):
            request = request.override(
                tools=[t for t in request.tools if getattr(t, "name", None) != _HUMAN_INPUT_TOOL]
            )
        return await super().awrap_model_call(request, handler)


class FinishPlanMiddleware(OpenSWEMiddleware[ReviewScoutState]):
    """Send whatever the scout left unplanned to Other, so the plan is complete."""

    state_schema = ReviewScoutState

    def __init__(self, *, thread_id: str, config: RunnableConfig) -> None:
        self._thread_id = thread_id
        self._config = config

    async def aafter_agent(self, state: ReviewScoutState, runtime: Runtime) -> None:  # noqa: ARG002
        cfg = RunConfig.from_config(self._config)
        if cfg.repo is None or cfg.pr_number is None or not cfg.head_sha:
            return
        extra = {
            "pr_repo_full_name": cfg.repo.full_name,
            "pr_number": cfg.pr_number,
            "scout_head_sha": cfg.head_sha,
        }
        try:
            workspace = await _workspace(get_cached_sandbox_backend(self._thread_id), cfg)
            settled = await workspace.settle()
        except PlanMovedError:
            logger.info(
                "Review scout head is stale; leaving the plan to the newer head", extra=extra
            )
            return
        except CheckoutError, PlannerUnavailableError:
            logger.exception("Review scout could not finish its plan", extra=extra)
            return
        if summary := state.get("human_input_summary", ""):
            await Walkthrough.set_human_input(workspace.pull_request.id, summary)
        await Topic.PULL_REQUESTS.invalidate(
            key=PullRequestKey.of(cfg.repo.owner, cfg.repo.name, cfg.pr_number)
        )
        logger.info(
            "Review scout finished its plan",
            extra={
                **extra,
                "scout_chunks": len(workspace.plan.chunks),
                "scout_settled_lines": settled,
            },
        )


def _make_model_or_defer(model_id: str, *, use_gateway: bool, **kwargs: Any) -> BaseChatModel:
    try:
        return make_model(model_id, use_gateway=use_gateway, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("Deferring review scout model setup failure for %s", model_id, exc_info=True)
        return make_deferred_error_model(e, model_id=model_id)


async def get_review_scout(config: RunnableConfig) -> Pregel:
    config = config.copy()
    configurable = dict(config.get("configurable") or {})
    config["configurable"] = configurable
    config.setdefault("recursion_limit", DEFAULT_RECURSION_LIMIT)
    cfg = RunConfig.parse(configurable)
    thread_id = cfg.thread_id

    if thread_id is None or not graph_loaded_for_execution(config):
        return create_deep_agent(system_prompt="", tools=[]).with_config(bindable_config(config))

    settings = await cached_workspace_settings(cfg.workspace_slug)
    model_id, effort = settings.review_scout_model
    model_id, effort = gate_fable_model(model_id, effort, fable_enabled=settings.fable_enabled)
    model = _make_model_or_defer(
        model_id,
        use_gateway=settings.effective_gateway_enabled,
        **provider_model_kwargs(
            model_id,
            effort,
            max_tokens=DEFAULT_LLM_MAX_TOKENS,
            openai_reasoning_default=DEFAULT_LLM_REASONING,
        ),
    )

    async def reconnect_backend(
        _thread_id: str = thread_id, _cfg: RunConfig = cfg
    ) -> SandboxBackendProtocol:
        return await _ensure_scout_sandbox(_thread_id, _cfg)

    return create_deep_agent(
        model=model,
        system_prompt="",
        tools=apply_tool_descriptions(
            [
                walkthrough_plan_chunk,
                walkthrough_move_to_other,
                walkthrough_describe_other,
                record_human_input,
            ]
        ),
        backend=get_cached_sandbox_backend(thread_id, reconnect=reconnect_backend),
        middleware=cast(
            list[AgentMiddleware[Any, Any, Any]],
            [
                PrepareReviewScoutRunMiddleware(thread_id=thread_id, config=config),
                ModelCallLimitMiddleware(run_limit=SCOUT_MODEL_CALL_LIMIT, exit_behavior="end"),
                ToolErrorMiddleware(),
                SanitizeFireworksMessagesMiddleware(),
                SanitizeOpenAIResponsesMiddleware(),
                SanitizeThinkingBlocksMiddleware(),
                RepairOrphanedToolCallsMiddleware(),
                StableToolResultOrderMiddleware(),
                ModelRetryMiddleware(retry_on=(TimeoutError,)),
                ModelErrorMiddleware(),
                ModelCallTimeoutMiddleware(),
                FinishPlanMiddleware(thread_id=thread_id, config=config),
            ],
        ),
    ).with_config(bindable_config(config))


# langgraph.json entrypoint. Runs trace into LANGSMITH_PROJECT like everything else.
traced_review_scout = get_review_scout
