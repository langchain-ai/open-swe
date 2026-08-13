"""Analyzer graph.

Learns a per-repo review-style prompt for the reviewer agent. It mines
historical human PR review feedback and this reviewer's own past finding
outcomes (resolved / dismissed / 👍👎) to teach what this team flags and skips.

Uses the same sandbox + ``gh`` pattern as the reviewer agent. The dashboard
user's OAuth token is injected into the LangSmith GitHub proxy so ``gh`` works
on public repos even when the GitHub App is not installed on them.
"""
# ruff: noqa: E402

from __future__ import annotations

import logging
import os
import warnings
from typing import Any, cast

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph.runtime import Runtime

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from deepagents import create_deep_agent
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import SandboxBackendProtocol
from deepagents.backends.state import StateBackend
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel

from .dashboard.team_settings import get_effective_gateway_enabled
from .integrations.langsmith import _configure_github_proxy
from .middleware import (
    BasePrepareRunMiddleware,
    PrepareRunState,
    SanitizeToolInputsMiddleware,
    TimeoutWrapupMiddleware,
    ToolErrorMiddleware,
)
from .review.style_guidance import REVIEWER_STYLE_THEMES
from .runtime import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_MODEL_ID,
    DEFAULT_RECURSION_LIMIT,
    ensure_sandbox_for_thread,
    get_cached_sandbox_backend,
    graph_loaded_for_execution,
)
from .tools.read_finding_outcomes import read_finding_outcomes
from .tools.save_review_style import save_review_style_prompt
from .utils import ttl_cache
from .utils.analyzer_skills import SKILLS_ROUTE, skill_path_for_mode
from .utils.deferred_model import make_deferred_error_model
from .utils.github_app import get_github_app_installation_token
from .utils.model import DEFAULT_LLM_REASONING, make_model, provider_model_kwargs
from .utils.sandbox_paths import aresolve_sandbox_work_dir
from .utils.sandbox_state import unwrap_sandbox_backend
from .utils.tracing import REVIEW_TRACING_PROJECT, traced_graph_factory

logger = logging.getLogger(__name__)

STYLE_ANALYZER_MODEL_CALL_LIMIT = 80

# The per-mode procedure lives in the bundled SKILL.md playbooks (agent/skills/).
# This base prompt only orients the agent and points it at the right skill.
STYLE_ANALYZER_PROMPT = """You are a code-review style analyst for `{repo_owner}/{repo_name}`.

Sandbox: `{working_dir}`. Use the shell (``execute``) to run GitHub commands.
**Always invoke gh as:** `GH_TOKEN=dummy gh <command>`.

Your job is to produce/refine the per-repo review-style prompt and persist it with
`save_review_style_prompt`.

# Run mode: {mode}

Read and follow the playbook for this mode, then proceed:

    read_file("{skill_path}", limit=1000)

Do not improvise the procedure — the skill is authoritative for how to gather
evidence and what to save.

# Alignment with our reviewer agent

{reviewer_themes}
"""


async def _configure_sandbox_github_proxy(
    sandbox_backend: SandboxBackendProtocol,
    github_token: str,
) -> None:
    if os.getenv("SANDBOX_TYPE", "langsmith") != "langsmith":
        return
    backend = unwrap_sandbox_backend(sandbox_backend)
    await _configure_github_proxy(backend.id, github_token)


async def _cached_gateway_enabled() -> bool:
    return await ttl_cache.cached(
        f"team:gateway-enabled:{id(get_effective_gateway_enabled)}",
        60,
        get_effective_gateway_enabled,
    )


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
        configurable = self._config.get("configurable", {})
        return {
            "prepare_run_id": configurable.get("prepare_run_id")
            if isinstance(configurable, dict)
            else None,
            "thread_id": self._thread_id,
            "full_name": configurable.get("review_style_full_name")
            if isinstance(configurable, dict)
            else None,
            "mode": configurable.get("analyzer_mode") if isinstance(configurable, dict) else None,
        }

    async def _prepare(self, state: PrepareRunState, runtime: Runtime) -> dict[str, Any]:  # noqa: ARG002
        sandbox_backend = await ensure_sandbox_for_thread(self._thread_id)
        work_dir = await aresolve_sandbox_work_dir(sandbox_backend)
        configurable = self._config.get("configurable") or {}
        full_name = str(configurable.get("review_style_full_name") or "owner/repo")
        owner, _, name = full_name.partition("/")
        samples_text = str(configurable.get("review_style_samples_text") or "")
        mode = str(configurable.get("analyzer_mode") or "bootstrap")
        github_token = configurable.get("review_style_github_token")
        if not (isinstance(github_token, str) and github_token):
            github_token = await get_github_app_installation_token()
        if isinstance(github_token, str) and github_token:
            await _configure_sandbox_github_proxy(sandbox_backend, github_token)
        system_prompt = STYLE_ANALYZER_PROMPT.format(
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
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")
    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    if thread_id is None or not graph_loaded_for_execution(config):
        return create_deep_agent(system_prompt="", tools=[]).with_config(config)

    async def reconnect_backend(_thread_id: str = thread_id):
        return await ensure_sandbox_for_thread(_thread_id)

    default_backend = get_cached_sandbox_backend(thread_id, reconnect=reconnect_backend)
    backend = CompositeBackend(default=default_backend, routes={SKILLS_ROUTE: StateBackend()})

    model_id = DEFAULT_LLM_MODEL_ID
    use_gateway = await _cached_gateway_enabled()
    model_kwargs = provider_model_kwargs(
        model_id,
        None,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
        openai_reasoning_default=DEFAULT_LLM_REASONING,
    )

    return create_deep_agent(
        model=_make_model_or_defer(model_id, use_gateway=use_gateway, **model_kwargs),
        system_prompt="",
        tools=[save_review_style_prompt, read_finding_outcomes],
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
            ],
        ),
    ).with_config(config)


traced_analyzer = traced_graph_factory(get_analyzer, REVIEW_TRACING_PROJECT)
