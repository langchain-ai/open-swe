"""Review scout graph.

Cuts a pull request's full diff into an ordered series of commits a reviewer
can read top to bottom and stores them as the PR's walkthrough, alongside a
summary of the human input behind it. Runs on its own thread per PR; the
reviewer starts it and waits for it before reviewing.
"""

import logging
import re
from typing import Any, NotRequired, cast

from deepagents import create_deep_agent
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph.runtime import Runtime

from agent.dashboard.options import gate_fable_model
from agent.dashboard.workspace_settings_cache import cached_workspace_settings
from agent.github.app import get_github_app_installation_token_with_expiry
from agent.github.thread_token import cache_github_token_for_thread
from agent.middleware import (
    BasePrepareRunMiddleware,
    ModelCallTimeoutMiddleware,
    ModelErrorMiddleware,
    RepairOrphanedToolCallsMiddleware,
    SanitizeFireworksMessagesMiddleware,
    SanitizeOpenAIResponsesMiddleware,
    SanitizeThinkingBlocksMiddleware,
    SanitizeToolInputsMiddleware,
    StableToolResultOrderMiddleware,
    TimeoutWrapupMiddleware,
    ToolErrorMiddleware,
)
from agent.middleware.prepare_run import PrepareRunState
from agent.middleware.trace import OpenSWEMiddleware
from agent.prompts import apply_tool_descriptions, prompt
from agent.review.author_guidance import SteeringHistory
from agent.review.walkthrough import Walkthrough
from agent.review_scout.git import ScoutGitError, finalize, setup_working_tree
from agent.review_scout.paths import scout_repo_dir
from agent.run_config import RunConfig
from agent.runtime import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_RECURSION_LIMIT,
    bindable_config,
    ensure_sandbox_for_thread,
    get_cached_sandbox_backend,
    graph_loaded_for_execution,
)
from agent.sandboxes.repo_prep import prepare_review_repo
from agent.tools.commit_walkthrough_step import commit_walkthrough_step
from agent.tools.record_human_input import record_human_input
from agent.utils.deferred_model import make_deferred_error_model
from agent.utils.model import DEFAULT_LLM_REASONING, make_model, provider_model_kwargs

logger = logging.getLogger(__name__)

SCOUT_MODEL_CALL_LIMIT = 150
_CLOSING_TITLE_TAG_RE = re.compile(r"</\s*pr_title\s*>", re.IGNORECASE)
MAX_STEPS = 8


class ReviewScoutState(PrepareRunState):
    scout_merge_base: NotRequired[str | None]
    human_input_summary: NotRequired[str]


async def _ensure_scout_sandbox(thread_id: str, cfg: RunConfig) -> SandboxBackendProtocol:
    repo_name = cfg.repo.name if cfg.repo else ""
    token, expires_at = await get_github_app_installation_token_with_expiry(
        repositories=[repo_name] if repo_name else None
    )
    if not token:
        raise RuntimeError(
            f"GitHub App installation token unavailable for scout thread {thread_id}"
        )
    cache_github_token_for_thread(thread_id, token, expires_at=expires_at, is_bot_token=True)
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
        repo_dir = await scout_repo_dir(backend, cfg)
        if repo_dir is None:
            raise RuntimeError("review scout repository name is invalid")
        work_dir = repo_dir.rsplit("/", 1)[0]
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
        merge_base = await setup_working_tree(
            backend, repo_dir, base_sha=cfg.base_sha, head_sha=cfg.head_sha
        )
        system_prompt = prompt(
            "review-scout/main",
            pr_number=cfg.pr_number,
            repo_full_name=cfg.repo.full_name,
            pr_title=_CLOSING_TITLE_TAG_RE.sub("</pr_title_>", cfg.pr_title or ""),
            repo_dir=repo_dir,
            merge_base=merge_base,
            patch_dir=f"{work_dir}/.scout-patches",
            max_steps=MAX_STEPS,
        )
        history = await SteeringHistory.load(cfg.repo.owner, cfg.repo.name, cfg.pr_number)
        if history is not None:
            human_input = prompt("review-scout/human-input", messages=history.messages_block())
            system_prompt = f"{system_prompt}\n\n{human_input}"
        return {
            "work_dir": work_dir,
            "rendered_system_prompt": system_prompt,
            "scout_merge_base": merge_base,
            "human_input_summary": "",
        }


class StoreWalkthroughMiddleware(OpenSWEMiddleware[ReviewScoutState]):
    """Close the scout's commits on the PR head and store them as the walkthrough."""

    state_schema = ReviewScoutState

    def __init__(self, *, thread_id: str, config: RunnableConfig) -> None:
        self._thread_id = thread_id
        self._config = config

    async def aafter_agent(self, state: ReviewScoutState, runtime: Runtime) -> None:  # noqa: ARG002
        cfg = RunConfig.from_config(self._config)
        merge_base = state.get("scout_merge_base")
        if not merge_base or cfg.repo is None or cfg.pr_number is None or not cfg.head_sha:
            return
        extra = {
            "pr_repo_full_name": cfg.repo.full_name,
            "pr_number": cfg.pr_number,
            "scout_head_sha": cfg.head_sha,
        }
        backend = get_cached_sandbox_backend(self._thread_id)
        repo_dir = await scout_repo_dir(backend, cfg)
        if repo_dir is None:
            return
        try:
            steps = await finalize(backend, repo_dir, merge_base=merge_base, head_sha=cfg.head_sha)
        except ScoutGitError:
            logger.exception("Review scout could not finalize its walkthrough", extra=extra)
            return
        if not any(not step.is_other for step in steps):
            logger.warning("Review scout committed no walkthrough steps", extra=extra)
            return
        leftover = next((step for step in steps if step.is_other), None)
        if leftover is not None:
            logger.info(
                "Review scout walkthrough has an other step",
                extra={**extra, "scout_other_files": len(leftover.files)},
            )
        await Walkthrough.replace(
            cfg.repo.owner,
            cfg.repo.name,
            cfg.pr_number,
            head_sha=cfg.head_sha,
            merge_base_sha=merge_base,
            scout_thread_id=self._thread_id,
            steps=steps,
            human_input_summary=state.get("human_input_summary", ""),
        )
        logger.info("Stored review walkthrough", extra={**extra, "scout_steps": len(steps)})


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
        tools=apply_tool_descriptions([commit_walkthrough_step, record_human_input]),
        backend=get_cached_sandbox_backend(thread_id, reconnect=reconnect_backend),
        middleware=cast(
            list[AgentMiddleware[Any, Any, Any]],
            [
                PrepareReviewScoutRunMiddleware(thread_id=thread_id, config=config),
                SanitizeToolInputsMiddleware(),
                ModelCallLimitMiddleware(run_limit=SCOUT_MODEL_CALL_LIMIT, exit_behavior="end"),
                ToolErrorMiddleware(),
                TimeoutWrapupMiddleware(),
                SanitizeFireworksMessagesMiddleware(),
                SanitizeOpenAIResponsesMiddleware(),
                SanitizeThinkingBlocksMiddleware(),
                RepairOrphanedToolCallsMiddleware(),
                StableToolResultOrderMiddleware(),
                ModelErrorMiddleware(),
                ModelCallTimeoutMiddleware(),
                StoreWalkthroughMiddleware(thread_id=thread_id, config=config),
            ],
        ),
    ).with_config(bindable_config(config))


# langgraph.json entrypoint. Runs trace into LANGSMITH_PROJECT like everything else.
traced_review_scout = get_review_scout
