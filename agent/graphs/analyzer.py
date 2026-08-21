"""Analyzer graph.

Learns a per-repo review-style prompt for the reviewer agent. It mines
historical human PR review feedback and this reviewer's own past finding
outcomes (resolved / dismissed / 👍👎) to teach what this team flags and skips.

Uses the same sandbox + ``gh`` pattern as the reviewer agent. The dashboard
user's OAuth token is injected into the LangSmith GitHub proxy so ``gh`` works
on public repos even when the GitHub App is not installed on them.
"""

import logging
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.state import StateBackend
from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph.runtime import Runtime

from ..middleware import (
    BasePrepareRunMiddleware,
    DynamicContextMiddleware,
    PrepareRunState,
    TimeoutWrapupMiddleware,
)
from ..middleware.stack import core_stack
from ..review.style_guidance import REVIEWER_STYLE_THEMES
from ..runtime import (
    DEFAULT_LLM_MODEL_ID,
    ensure_sandbox_for_thread,
    get_cached_sandbox_backend,
    graph_loaded_for_execution,
)
from ..tools.read_finding_outcomes import read_finding_outcomes
from ..tools.save_review_style import save_review_style_prompt
from ..utils.analyzer_skills import SKILLS_ROUTE, skill_path_for_mode
from ..utils.github_app import get_github_app_installation_token
from ..utils.github_proxy import configure_proxy_for_sandbox
from ..utils.model import DEFAULT_LLM_REASONING
from ..utils.sandbox_paths import aresolve_sandbox_work_dir
from ..utils.tracing import REVIEW_TRACING_PROJECT, traced_graph_factory
from ._assembly import cached_gateway_enabled, model_spec, prepare_config, stub_agent

logger = logging.getLogger(__name__)

STYLE_ANALYZER_MODEL_CALL_LIMIT = 80

# The per-mode procedure lives in the bundled SKILL.md playbooks (agent/skills/).
# This base prompt only orients the agent and points it at the right skill.
STYLE_ANALYZER_PROMPT = """You are a code-review style analyst for `{repo_owner}/{repo_name}`.

Sandbox: `{working_dir}`. Use the shell (``execute``) to run GitHub commands.
`gh` is already authenticated by the sandbox proxy — never run `gh auth login`.

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
            await configure_proxy_for_sandbox(
                sandbox_backend, thread_id=self._thread_id, github_token=github_token
            )
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
    config, configurable = prepare_config(config)
    thread_id = configurable.get("thread_id")

    if thread_id is None or not graph_loaded_for_execution(config):
        return stub_agent(config)

    async def reconnect_backend(_thread_id: str = thread_id):
        return await ensure_sandbox_for_thread(_thread_id)

    default_backend = get_cached_sandbox_backend(thread_id, reconnect=reconnect_backend)
    backend = CompositeBackend(default=default_backend, routes={SKILLS_ROUTE: StateBackend()})

    spec = model_spec(DEFAULT_LLM_MODEL_ID, None, openai_reasoning_default=DEFAULT_LLM_REASONING)

    return create_deep_agent(
        model=spec.build(use_gateway=await cached_gateway_enabled()),
        system_prompt="",
        tools=[save_review_style_prompt, read_finding_outcomes],
        backend=backend,
        skills=[SKILLS_ROUTE],
        middleware=core_stack(
            PrepareAnalyzerRunMiddleware(thread_id=thread_id, config=config),
            call_limit=STYLE_ANALYZER_MODEL_CALL_LIMIT,
            extras=[TimeoutWrapupMiddleware(), DynamicContextMiddleware()],
            # No refresh_github_proxy_before_model: an analyzer run may be
            # carrying a dashboard user's OAuth token in the sandbox proxy and
            # must not be swapped onto an App token mid-run.
        ),
    ).with_config(config)


traced_analyzer = traced_graph_factory(get_analyzer, REVIEW_TRACING_PROJECT)
