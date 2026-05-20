"""Babysitter graph factory for keeping a PR healthy."""

# ruff: noqa: E402

from __future__ import annotations

import logging
import os
import warnings
from typing import Any

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel

logger = logging.getLogger(__name__)

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from deepagents import create_deep_agent
from langchain.agents.middleware import ModelCallLimitMiddleware

from .middleware import (
    SanitizeToolInputsMiddleware,
    SlackAssistantStatusMiddleware,
    ToolErrorMiddleware,
    check_message_queue_before_model,
    ensure_no_empty_msg,
)
from .server import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_MODEL_ID,
    DEFAULT_LLM_REASONING,
    DEFAULT_RECURSION_LIMIT,
    MODEL_CALL_RECURSION_LIMIT,
    _anthropic_effort_for,
    _anthropic_thinking_for,
    _openai_reasoning_for,
    ensure_sandbox_for_thread,
    graph_loaded_for_execution,
)
from .utils.auth import resolve_github_token
from .utils.github_token import get_github_token_from_thread
from .utils.model import ModelKwargs, make_model
from .utils.sandbox_paths import aresolve_sandbox_work_dir

BABYSITTER_PROMPT_TEMPLATE = """You are Open SWE's PR babysitter.

You keep one GitHub pull request healthy by watching CI and review feedback,
making small targeted fixes, pushing them to the PR branch, and reporting what
you did.

Working directory: `{working_dir}`.

GitHub access:
- The `gh` CLI is installed and authenticated by a sandbox proxy. Always invoke
  it as `GH_TOKEN=dummy gh <command>`.
- Target PR: {repo_owner}/{repo_name}#{pr_number}
- PR URL: {pr_url}

Workflow:
1. Inspect the PR, current head, checks, and recent comments.
2. If CI failed, fetch failed logs (`gh pr checks`, `gh run view --log-failed`)
   and reproduce locally when practical.
3. If there are review comments, only act on concrete requested changes.
4. Clone or update the repo, checkout the PR branch/head, make the smallest
   correct fix, and run focused validation.
5. Commit and push to the PR branch. Never force-push. Never merge.
6. Leave a concise PR comment summarizing fixes and validation.

Guardrails:
- Do not modify unrelated files.
- Ignore comments authored by Open SWE.
- If the failure is flaky, infrastructure-related, blocked on secrets, or
  needs product/design judgment, comment with what you found instead of editing.
- Stop after one focused fix attempt for this run.
"""


def _system_prompt(working_dir: str, configurable: dict[str, Any]) -> str:
    repo = configurable.get("repo") if isinstance(configurable.get("repo"), dict) else {}
    return BABYSITTER_PROMPT_TEMPLATE.format(
        working_dir=working_dir,
        repo_owner=repo.get("owner", "<owner>"),
        repo_name=repo.get("name", "<repo>"),
        pr_number=configurable.get("pr_number", "<pr_number>"),
        pr_url=configurable.get("pr_url", ""),
    )


async def get_babysitter_agent(config: RunnableConfig) -> Pregel:
    """Get or create a babysitter agent with a sandbox."""
    thread_id = config["configurable"].get("thread_id", None)
    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    if thread_id is None or not graph_loaded_for_execution(config):
        logger.info("No thread_id or not for execution, returning babysitter without sandbox")
        return create_deep_agent(system_prompt="", tools=[]).with_config(config)

    if config["configurable"].get("source"):
        cached_token, cached_encrypted, cached_expires_at = await get_github_token_from_thread(
            thread_id
        )
        if cached_token and cached_encrypted:
            config["metadata"]["github_token_encrypted"] = cached_encrypted
            config["metadata"]["github_token_expires_at"] = cached_expires_at
            del cached_token
        else:
            _token, new_encrypted, new_expires_at = await resolve_github_token(config, thread_id)
            config["metadata"]["github_token_encrypted"] = new_encrypted
            config["metadata"]["github_token_expires_at"] = new_expires_at
            del _token

    sandbox_backend = await ensure_sandbox_for_thread(thread_id)
    work_dir = await aresolve_sandbox_work_dir(sandbox_backend)

    configurable = config["configurable"]
    repo = configurable.get("repo") if isinstance(configurable.get("repo"), dict) else {}
    repo_name = str(repo.get("name", ""))

    configured_model_id = configurable.get("babysitter_model_id")
    model_id = (
        configured_model_id
        if isinstance(configured_model_id, str) and configured_model_id
        else os.environ.get("LLM_MODEL_ID", DEFAULT_LLM_MODEL_ID)
    )
    configured_effort = configurable.get("babysitter_reasoning_effort")
    reasoning_effort = configured_effort if isinstance(configured_effort, str) else None
    model_kwargs: ModelKwargs = {"max_tokens": DEFAULT_LLM_MAX_TOKENS}
    if model_id.startswith("openai:"):
        reasoning = _openai_reasoning_for(reasoning_effort)
        model_kwargs["reasoning"] = reasoning if reasoning is not None else DEFAULT_LLM_REASONING
    elif model_id.startswith("anthropic:"):
        thinking = _anthropic_thinking_for(reasoning_effort)
        if thinking is not None:
            model_kwargs["thinking"] = thinking
        effort = _anthropic_effort_for(reasoning_effort)
        if effort is not None:
            model_kwargs["effort"] = effort

    return create_deep_agent(
        model=make_model(model_id, **model_kwargs),
        system_prompt=_system_prompt(
            f"{work_dir}/{repo_name}" if repo_name else work_dir, configurable
        ),
        tools=[],
        backend=sandbox_backend,
        middleware=[
            SanitizeToolInputsMiddleware(),
            ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"),
            ToolErrorMiddleware(),
            check_message_queue_before_model,
            ensure_no_empty_msg,
            SlackAssistantStatusMiddleware(),
        ],
    ).with_config(config)
