"""Analyzer graph.

Learns a per-repo review-style prompt for the reviewer agent. It mines
historical human PR review feedback and this reviewer's own past finding
outcomes (resolved / dismissed / 👍👎) to teach what this team flags and skips.

Uses workspace-scoped sandbox credentials, like the coding agent. Public
repositories outside the workspace remain readable anonymously.
"""

# ruff: noqa: E402
import logging
import warnings
from typing import Any, cast

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph.runtime import Runtime

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from deepagents import create_deep_agent
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.state import StateBackend
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel

from agent.dashboard.workspace_settings_cache import cached_workspace_settings
from agent.middleware import (
    BasePrepareRunMiddleware,
    PrepareRunState,
    SanitizeOpenAIResponsesMiddleware,
    SanitizeToolInputsMiddleware,
    TimeoutWrapupMiddleware,
    ToolErrorMiddleware,
)
from agent.prompts import apply_tool_descriptions, prompt
from agent.review.style_guidance import REVIEWER_STYLE_THEMES
from agent.run_config import RunConfig
from agent.runtime import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_MODEL_ID,
    DEFAULT_RECURSION_LIMIT,
    bindable_config,
    ensure_sandbox_for_thread,
    get_cached_sandbox_backend,
    graph_loaded_for_execution,
)
from agent.sandboxes.paths import resolve_sandbox_work_dir
from agent.tools.read_finding_outcomes import read_finding_outcomes
from agent.tools.save_review_style import save_review_style_prompt
from agent.utils.analyzer_skills import SKILLS_ROUTE, skill_path_for_mode
from agent.utils.deferred_model import make_deferred_error_model
from agent.utils.model import DEFAULT_LLM_REASONING, make_model, provider_model_kwargs

logger = logging.getLogger(__name__)

STYLE_ANALYZER_MODEL_CALL_LIMIT = 80


async def _analyzer_workspace(cfg: RunConfig) -> str | None:
    if cfg.workspace_slug or not cfg.review_style_full_name:
        return cfg.workspace_slug
    from agent.workspaces.store import WORKSPACES

    if WORKSPACES.repo_import_is_pending(cfg.review_style_full_name):
        raise RuntimeError("Analyzer repository workspace has not been imported")
    return await WORKSPACES.owner_of_repo(cfg.review_style_full_name)


def _make_model_or_defer(model_id: str, *, use_gateway: bool, **kwargs: Any) -> BaseChatModel:
    try:
        return make_model(model_id, use_gateway=use_gateway, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("Deferring analyzer model setup failure for %s", model_id, exc_info=True)
        return make_deferred_error_model(e, model_id=model_id)


class PrepareAnalyzerRunMiddleware(BasePrepareRunMiddleware):
    def __init__(self, *, thread_id: str, config: RunnableConfig) -> None:
        self._thread_id = thread_id
        self._config = config

    def _prepare_config_fingerprint(self) -> object:
        cfg = RunConfig.from_config(self._config)
        return {
            "invocation_id": cfg.invocation_id,
            "thread_id": self._thread_id,
            "full_name": cfg.review_style_full_name,
            "mode": cfg.analyzer_mode,
        }

    async def _prepare(self, state: PrepareRunState, runtime: Runtime) -> dict[str, Any]:  # noqa: ARG002
        cfg = RunConfig.from_config(self._config)
        sandbox_backend = await ensure_sandbox_for_thread(
            self._thread_id,
            workspace_slug=await _analyzer_workspace(cfg),
            github_proxy_repositories=[cfg.review_style_full_name]
            if cfg.review_style_full_name
            else [],
        )
        work_dir = await resolve_sandbox_work_dir(sandbox_backend)
        full_name = cfg.review_style_full_name or "owner/repo"
        owner, _, name = full_name.partition("/")
        samples_text = cfg.review_style_samples_text or ""
        mode = cfg.analyzer_mode or "bootstrap"
        system_prompt = prompt(
            "analyzer/main",
            repo_owner=owner or "<owner>",
            repo_name=name or "<repo>",
            working_dir=work_dir,
            mode=mode,
            skill_path=skill_path_for_mode(mode),
            reviewer_themes=REVIEWER_STYLE_THEMES.strip(),
        )
        user_context = f"Repository: `{full_name}`\n\n{samples_text}".strip()
        return {
            "work_dir": work_dir,
            "rendered_system_prompt": f"{system_prompt}\n\n{user_context}",
        }


async def get_analyzer(config: RunnableConfig) -> Pregel:
    cfg = RunConfig.from_config(config)
    thread_id = cfg.thread_id
    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    if thread_id is None or not graph_loaded_for_execution(config):
        return create_deep_agent(system_prompt="", tools=[]).with_config(bindable_config(config))

    workspace = await _analyzer_workspace(cfg)

    async def reconnect_backend(_thread_id: str = thread_id):
        return await ensure_sandbox_for_thread(
            _thread_id,
            workspace_slug=workspace,
            github_proxy_repositories=[cfg.review_style_full_name]
            if cfg.review_style_full_name
            else [],
        )

    default_backend = get_cached_sandbox_backend(thread_id, reconnect=reconnect_backend)
    backend = CompositeBackend(default=default_backend, routes={SKILLS_ROUTE: StateBackend()})

    model_id = DEFAULT_LLM_MODEL_ID
    use_gateway = (await cached_workspace_settings(workspace)).effective_gateway_enabled
    model_kwargs = provider_model_kwargs(
        model_id,
        None,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
        openai_reasoning_default=DEFAULT_LLM_REASONING,
    )

    return create_deep_agent(
        model=_make_model_or_defer(model_id, use_gateway=use_gateway, **model_kwargs),
        system_prompt="",
        tools=apply_tool_descriptions([save_review_style_prompt, read_finding_outcomes]),
        backend=backend,
        skills=[SKILLS_ROUTE],
        middleware=cast(
            list[AgentMiddleware[Any, Any, Any]],
            [
                PrepareAnalyzerRunMiddleware(thread_id=thread_id, config=config),
                SanitizeToolInputsMiddleware(),
                ModelCallLimitMiddleware(
                    run_limit=STYLE_ANALYZER_MODEL_CALL_LIMIT,
                    exit_behavior="end",
                ),
                ToolErrorMiddleware(),
                TimeoutWrapupMiddleware(),
                SanitizeOpenAIResponsesMiddleware(),
            ],
        ),
    ).with_config(bindable_config(config))


# langgraph.json entrypoint. Runs trace into LANGSMITH_PROJECT like everything else.
traced_analyzer = get_analyzer
