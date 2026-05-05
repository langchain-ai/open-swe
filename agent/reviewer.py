"""Reviewer graph factory.

Mirrors `agent.server.get_agent`'s sandbox lifecycle but returns a deep agent
configured for code review only: narrowed tool set, reviewer-specific system
prompt, no commit/push/PR-opening.

Inline review comments are recorded by the agent calling the `github_comment`
tool — one call per distinct issue. The eval harness extracts those calls
from the run's message stream.
"""
# ruff: noqa: E402

import asyncio
import logging
import os
import warnings

logger = logging.getLogger(__name__)

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph_sdk import get_client

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from deepagents import create_deep_agent
from deepagents.middleware._tool_exclusion import _ToolExclusionMiddleware
from langchain.agents.middleware import ModelCallLimitMiddleware

from .middleware import (
    SanitizeToolInputsMiddleware,
    ToolErrorMiddleware,
)
from .server import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_LLM_MODEL_ID,
    DEFAULT_LLM_REASONING,
    DEFAULT_RECURSION_LIMIT,
    MODEL_CALL_RECURSION_LIMIT,
    SANDBOX_CREATING,
    _create_sandbox_with_proxy,
    _refresh_github_proxy,
    _wait_for_sandbox_id,
    check_or_recreate_sandbox,
    graph_loaded_for_execution,
)
from .tools import github_comment
from .utils.auth import resolve_github_token
from .utils.model import ModelKwargs, make_model
from .utils.sandbox import create_sandbox
from .utils.sandbox_paths import aresolve_sandbox_work_dir
from .utils.sandbox_state import SANDBOX_BACKENDS, get_sandbox_id_from_metadata

client = get_client()


REVIEWER_PROMPT_TEMPLATE = """You are an expert code reviewer.

Your job is to review a single GitHub pull request and surface real issues —
bugs, security problems, correctness errors, race conditions, performance
regressions, and clear quality issues. Do not nitpick style.

### Working environment

You are operating in a remote Linux sandbox at `{working_dir}`.

- The `gh` CLI is installed and authenticated by a sandbox proxy. Always
  invoke it as `GH_TOKEN=dummy gh <command>`.
- The `execute` tool runs shell commands. Default timeout is 300s; pass
  `timeout=<seconds>` if you need longer.

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
5. For each real issue you find, call the `github_comment` tool **once**
   with:
   - `file`: repo-relative path
   - `line`: 1-based line number in the new (post-PR) file
   - `body`: a specific description of the issue
   - `severity`: one of "Low", "Medium", "High", "Critical"

### Hard rules

- **You are read-only.** Do NOT commit. Do NOT push. Do NOT open or update
  PRs. Do NOT post comments via `gh pr comment`. The only way you record
  findings is by calling the `github_comment` tool.
- One `github_comment` call per distinct issue. Multiple calls per review
  are expected and correct.
- Do not summarize the PR in chat. Do not write a final review essay.
  Only `github_comment` calls are scored — anything else is ignored.
- If you find no real issues, make zero `github_comment` calls and stop.
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

    github_token, new_encrypted = await resolve_github_token(config, thread_id)
    config["metadata"]["github_token_encrypted"] = new_encrypted
    del github_token  # token consumed by the proxy refresh path; not used directly here

    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    sandbox_id = await get_sandbox_id_from_metadata(thread_id)

    if sandbox_id == SANDBOX_CREATING and not sandbox_backend:
        logger.info("Sandbox creation in progress, waiting...")
        sandbox_id = await _wait_for_sandbox_id(thread_id)

    if sandbox_backend:
        await _refresh_github_proxy(sandbox_backend)
        sandbox_backend = await check_or_recreate_sandbox(sandbox_backend, thread_id)
    elif sandbox_id is None:
        logger.info("Creating new reviewer sandbox for thread %s", thread_id)
        await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": SANDBOX_CREATING})
        try:
            sandbox_backend = await _create_sandbox_with_proxy()
        except Exception:
            logger.exception("Failed to create sandbox")
            await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
            raise
    else:
        logger.info("Connecting to existing sandbox %s", sandbox_id)
        try:
            sandbox_backend = await asyncio.to_thread(create_sandbox, sandbox_id)
        except Exception:
            logger.warning("Failed to connect to existing sandbox %s, creating new one", sandbox_id)
            await client.threads.update(
                thread_id=thread_id, metadata={"sandbox_id": SANDBOX_CREATING}
            )
            try:
                sandbox_backend = await _create_sandbox_with_proxy()
            except Exception:
                logger.exception("Failed to create replacement sandbox")
                await client.threads.update(thread_id=thread_id, metadata={"sandbox_id": None})
                raise
        await _refresh_github_proxy(sandbox_backend)
        sandbox_backend = await check_or_recreate_sandbox(sandbox_backend, thread_id)

    SANDBOX_BACKENDS[thread_id] = sandbox_backend

    if sandbox_id != sandbox_backend.id:
        await client.threads.update(
            thread_id=thread_id, metadata={"sandbox_id": sandbox_backend.id}
        )
        await asyncio.to_thread(
            sandbox_backend.execute,
            "git config --global user.name 'open-swe[bot]' && "
            "git config --global user.email 'open-swe@users.noreply.github.com'",
        )

    work_dir = await aresolve_sandbox_work_dir(sandbox_backend)

    model_id = os.environ.get("LLM_MODEL_ID", DEFAULT_LLM_MODEL_ID)
    model_kwargs: ModelKwargs = {"max_tokens": DEFAULT_LLM_MAX_TOKENS}
    if model_id == DEFAULT_LLM_MODEL_ID:
        model_kwargs["reasoning"] = DEFAULT_LLM_REASONING

    return create_deep_agent(
        model=make_model(model_id, **model_kwargs),
        system_prompt=_reviewer_system_prompt(work_dir),
        tools=[github_comment],
        backend=sandbox_backend,
        middleware=[
            SanitizeToolInputsMiddleware(),
            ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"),
            ToolErrorMiddleware(),
            _ToolExclusionMiddleware(excluded=frozenset({"task"})),
        ],
    ).with_config(config)
