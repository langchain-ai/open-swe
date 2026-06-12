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

import asyncio
import logging
import os
import warnings

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from deepagents import create_deep_agent
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import SandboxBackendProtocol
from deepagents.backends.state import StateBackend
from langchain.agents.middleware import ModelCallLimitMiddleware

from .integrations.langsmith import _configure_github_proxy
from .middleware import SanitizeToolInputsMiddleware, ToolErrorMiddleware
from .review_style_guidance import REVIEWER_STYLE_THEMES
from .server import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_MODEL_ID,
    DEFAULT_RECURSION_LIMIT,
    ensure_sandbox_for_thread,
    graph_loaded_for_execution,
)
from .tools.read_finding_outcomes import read_finding_outcomes
from .tools.save_review_style import save_review_style_prompt
from .utils.analyzer_skills import SKILLS_ROUTE, skill_path_for_mode
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
    await asyncio.to_thread(_configure_github_proxy, backend.id, github_token)


async def get_analyzer(config: RunnableConfig) -> Pregel:
    thread_id = config["configurable"].get("thread_id")
    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    if thread_id is None or not graph_loaded_for_execution(config):
        return create_deep_agent(system_prompt="", tools=[]).with_config(config)

    sandbox_backend = await ensure_sandbox_for_thread(thread_id)
    work_dir = await aresolve_sandbox_work_dir(sandbox_backend)

    configurable = config["configurable"]
    full_name = str(configurable.get("review_style_full_name") or "owner/repo")
    owner, _, name = full_name.partition("/")
    samples_text = str(configurable.get("review_style_samples_text") or "")
    mode = str(configurable.get("analyzer_mode") or "bootstrap")

    github_token = configurable.get("review_style_github_token")
    if not (isinstance(github_token, str) and github_token):
        # Nightly continual runs have no fresh dashboard OAuth token; fall back to
        # the GitHub App installation token so `gh` still works through the proxy.
        github_token = await get_github_app_installation_token()
    if isinstance(github_token, str) and github_token:
        await _configure_sandbox_github_proxy(sandbox_backend, github_token)

    # Skills are served from a virtual StateBackend route; gh/clone/execute stay on
    # the sandbox. SKILL.md files are seeded into the `files` channel at invoke time.
    backend = CompositeBackend(default=sandbox_backend, routes={SKILLS_ROUTE: StateBackend()})

    model_id = DEFAULT_LLM_MODEL_ID
    model_kwargs = provider_model_kwargs(
        model_id,
        None,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
        openai_reasoning_default=DEFAULT_LLM_REASONING,
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
    system_prompt = f"{system_prompt}\n\n{user_context}"

    return create_deep_agent(
        model=make_model(model_id, **model_kwargs),
        system_prompt=system_prompt,
        tools=[save_review_style_prompt, read_finding_outcomes],
        backend=backend,
        skills=[SKILLS_ROUTE],
        middleware=[
            SanitizeToolInputsMiddleware(),
            ModelCallLimitMiddleware(
                run_limit=STYLE_ANALYZER_MODEL_CALL_LIMIT,
                exit_behavior="end",
            ),
            ToolErrorMiddleware(),
        ],
    ).with_config(config)


traced_analyzer = traced_graph_factory(get_analyzer, REVIEW_TRACING_PROJECT)
