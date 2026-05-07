"""Reviewer graph factory.

Mirrors `agent.server.get_agent`'s sandbox lifecycle but returns a deep agent
configured for code review only: narrowed tool set, reviewer-specific system
prompt, no commit/push/PR-opening.

Inline review comments are submitted by the agent through the GitHub CLI.
"""
# ruff: noqa: E402

import logging
import os
import warnings

logger = logging.getLogger(__name__)

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from deepagents import create_deep_agent
from langchain.agents.middleware import ModelCallLimitMiddleware

from .middleware import (
    ExcludeToolsMiddleware,
    SanitizeToolInputsMiddleware,
    ToolErrorMiddleware,
)
from .server import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_MODEL_ID,
    DEFAULT_LLM_REASONING,
    DEFAULT_RECURSION_LIMIT,
    MODEL_CALL_RECURSION_LIMIT,
    ensure_sandbox_for_thread,
    graph_loaded_for_execution,
)
from .utils.auth import resolve_github_token
from .utils.github_token import get_github_token_from_thread
from .utils.model import ModelKwargs, make_model
from .utils.sandbox_paths import aresolve_sandbox_work_dir

REVIEWER_PROMPT_TEMPLATE = """You are an expert code reviewer.

Your job is to review a single GitHub pull request and surface real issues —
bugs, security problems, correctness errors, race conditions, performance
regressions, and clear quality issues. Do not nitpick style.

### Working environment

You are operating in a remote Linux sandbox at `{working_dir}`.

- The `gh` CLI is installed and authenticated by a sandbox proxy. Always
  invoke it as `GH_TOKEN=dummy gh <command>`.
- The `execute` tool runs shell commands. The default timeout is generous
  (~30 minutes); pass `timeout=<seconds>` only if you need to override it.

### How to review

1. The user message tells you which PR to review (URL, repo, PR number,
   base SHA, head SHA).
2. Clone the repo into `{working_dir}` and check out the **base SHA** so
   the working tree matches `main` at the time the PR was opened.
3. Fetch the PR head and inspect the diff:
   `GH_TOKEN=dummy gh pr diff <pr_number> --repo <owner>/<repo>`
   or use `git diff <base_sha>...<head_sha>`.
4. Read the files the PR changes — and any related files needed to
   understand the change in context. Use `read_file`, `grep`, `glob`.
5. For each real issue you find, submit one inline review comment with
   `GH_TOKEN=dummy gh api`:

   `GH_TOKEN=dummy gh api repos/<owner>/<repo>/pulls/<pr_number>/comments \
     -f body='<specific review comment>' \
     -f commit_id='<head_sha>' \
     -f path='<repo-relative file path>' \
     -F line=<new-file-line-number> \
     -f side=RIGHT`

   The `line` value must be a 1-based line number in the new post-PR file
   and must be part of the PR diff. If the issue spans multiple lines, anchor
   the comment to the most relevant changed line.

### Hard rules

- **You are read-only.** Do NOT commit. Do NOT push. Do NOT open or update
  PRs. Do NOT post top-level PR comments via `gh pr comment`.
- One inline `gh api` comment per distinct issue. Multiple comments per review
  are expected and correct.
- Do not summarize the PR in chat. Do not write a final review essay. Submit
  only inline review comments for real findings.
- If you find no real issues, submit no comments and stop.
"""


def _reviewer_system_prompt(working_dir: str) -> str:
    return REVIEWER_PROMPT_TEMPLATE.format(working_dir=working_dir)


async def get_reviewer_agent(config: RunnableConfig) -> Pregel:
    """Get or create a reviewer agent with a sandbox for the given thread."""
    thread_id = config["configurable"].get("thread_id", None)

    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    if thread_id is None or not graph_loaded_for_execution(config):
        logger.info("No thread_id or not for execution, returning reviewer agent without sandbox")
        return create_deep_agent(system_prompt="", tools=[]).with_config(config)

    if config["configurable"].get("source"):
        cached_token, cached_encrypted = await get_github_token_from_thread(thread_id)
        if cached_token and cached_encrypted:
            config["metadata"]["github_token_encrypted"] = cached_encrypted
            del cached_token
        else:
            _token, new_encrypted = await resolve_github_token(config, thread_id)
            config["metadata"]["github_token_encrypted"] = new_encrypted
            del _token

    sandbox_backend = await ensure_sandbox_for_thread(thread_id)

    work_dir = await aresolve_sandbox_work_dir(sandbox_backend)

    model_id = os.environ.get("LLM_MODEL_ID", DEFAULT_LLM_MODEL_ID)
    model_kwargs: ModelKwargs = {"max_tokens": DEFAULT_LLM_MAX_TOKENS}
    if model_id == DEFAULT_LLM_MODEL_ID:
        model_kwargs["reasoning"] = DEFAULT_LLM_REASONING

    return create_deep_agent(
        model=make_model(model_id, **model_kwargs),
        system_prompt=_reviewer_system_prompt(work_dir),
        tools=[],
        backend=sandbox_backend,
        middleware=[
            SanitizeToolInputsMiddleware(),
            ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"),
            ToolErrorMiddleware(),
            ExcludeToolsMiddleware(excluded=frozenset({"task"})),
        ],
    ).with_config(config)
