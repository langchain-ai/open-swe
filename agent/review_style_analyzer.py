"""Review style analyzer graph.

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

from . import _patch_messages_reducer
from ._patch_messages_reducer import _apply as _apply_messages_reducer_patch

_apply_messages_reducer_patch()

from deepagents import create_deep_agent
from deepagents.backends.protocol import SandboxBackendProtocol
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
from .tools.save_review_style import save_review_style_prompt
from .utils.model import DEFAULT_LLM_REASONING, make_model, provider_model_kwargs
from .utils.sandbox_paths import aresolve_sandbox_work_dir
from .utils.sandbox_state import unwrap_sandbox_backend

logger = logging.getLogger(__name__)

STYLE_ANALYZER_MODEL_CALL_LIMIT = 80

STYLE_ANALYZER_PROMPT = """You are a code-review style analyst for `{repo_owner}/{repo_name}`.

Sandbox: `{working_dir}`. Use the shell (``execute``) to run GitHub commands.

**Always invoke gh as:** `GH_TOKEN=dummy gh <command>`

# How to research (required)

Browse historical **merged** PR review feedback until you have catalogued at least
**8 substantive human** review comments (not bots). Suggested commands:

```
GH_TOKEN=dummy gh pr list --repo {repo_owner}/{repo_name} --state merged --limit 30
GH_TOKEN=dummy gh api repos/{repo_owner}/{repo_name}/pulls/<PR_NUMBER>/reviews
GH_TOKEN=dummy gh api repos/{repo_owner}/{repo_name}/pulls/<PR_NUMBER>/comments
GH_TOKEN=dummy gh api repos/{repo_owner}/{repo_name}/issues/<PR_NUMBER>/comments
```

If the first batch is sparse, increase `--limit` or walk older PR numbers. Skip
`[bot]` accounts and obvious automation (codecov, dependabot, etc.).

Identify the top ~5 human reviewers by volume and note phrasing, severity, and
what they ignore.

# When you may call `save_review_style_prompt`

Only after real research. Your `custom_prompt` (400–1200 words) must teach our
reviewer agent this repo's norms:

- What the team routinely flags vs skips (paraphrased patterns, not invented quotes)
- Severity calibration
- Tone and test expectations
- Repo-specific conventions
- Anti-patterns reviewers here avoid

`analysis_summary`: 2–4 sentences for the dashboard.
Pass `prs_sampled`, `reviews_sampled`, and `top_reviewers` (comma-separated logins).

Do **not** save a generic guide after one or two commands. Only after ~25+ merged
PRs with zero human feedback may you save a short conservative guide and say so in
`analysis_summary`.

# Alignment with our reviewer agent

{reviewer_themes}

# Optional preloaded samples

The user message may include pre-collected samples — verify and extend with ``gh``.
"""


async def _configure_sandbox_github_proxy(
    sandbox_backend: SandboxBackendProtocol,
    github_token: str,
) -> None:
    if os.getenv("SANDBOX_TYPE", "langsmith") != "langsmith":
        return
    backend = unwrap_sandbox_backend(sandbox_backend)
    await asyncio.to_thread(_configure_github_proxy, backend.id, github_token)


async def get_review_style_analyzer(config: RunnableConfig) -> Pregel:
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
    github_token = configurable.get("review_style_github_token")
    if isinstance(github_token, str) and github_token:
        await _configure_sandbox_github_proxy(sandbox_backend, github_token)

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
        reviewer_themes=REVIEWER_STYLE_THEMES.strip(),
    )
    user_context = (
        f"Repository: `{full_name}`\n\n"
        f"{samples_text}\n\n"
        "Research review style with `GH_TOKEN=dummy gh ...` via execute, then call "
        "`save_review_style_prompt` once you have enough evidence."
    )
    system_prompt = f"{system_prompt}\n\n{user_context}"

    return create_deep_agent(
        model=make_model(model_id, **model_kwargs),
        system_prompt=system_prompt,
        tools=[save_review_style_prompt],
        backend=sandbox_backend,
        middleware=[
            SanitizeToolInputsMiddleware(),
            ModelCallLimitMiddleware(
                run_limit=STYLE_ANALYZER_MODEL_CALL_LIMIT,
                exit_behavior="end",
            ),
            ToolErrorMiddleware(),
        ],
    ).with_config(config)
