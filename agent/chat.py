"""Chat graph.

A read-only "chat with this PR" agent for the review UI. Unlike the main agent
and reviewer, it has **no sandbox**: it answers questions about a single pull
request using the diff, the published review findings, and read-only access to
the repository over the GitHub API.

PR context (diff, findings, overview) is seeded as virtual files under ``/pr/``
into the ``files`` state channel by the dashboard chat proxy
(``agent/dashboard/review_chat_api.py``); the built-in ``read_file``/``grep``
tools operate over those. Repo coordinates and the reviewer thread id arrive in
``configurable``; a repo-scoped GitHub App token is resolved here so the
GitHub-backed tools never receive a user credential.
"""
# ruff: noqa: E402

from __future__ import annotations

import logging
import warnings

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from deepagents import create_deep_agent
from langchain.agents.middleware import ModelCallLimitMiddleware

from .dashboard.options import SUPPORTED_MODEL_IDS, model_supports_effort
from .dashboard.team_settings import get_effective_gateway_enabled, get_team_default_model
from .middleware import (
    BasePrepareRunMiddleware,
    ExcludeToolsMiddleware,
    SanitizeFireworksMessagesMiddleware,
    SanitizeThinkingBlocksMiddleware,
    SanitizeToolInputsMiddleware,
    ToolErrorMiddleware,
)
from .server import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_RECURSION_LIMIT,
    graph_loaded_for_execution,
)
from .tools import (
    fetch_url,
    list_review_findings,
    read_repo_file,
    search_repo_code,
    web_search,
)
from .utils import ttl_cache
from .utils.github_app import get_github_app_installation_token
from .utils.model import DEFAULT_LLM_REASONING, make_model, provider_model_kwargs
from .utils.tracing import AGENT_TRACING_PROJECT, traced_graph_factory

logger = logging.getLogger(__name__)

CHAT_MODEL_CALL_LIMIT = 100

# Read-only: the chat agent never mutates files or runs shell commands. These are
# injected by deepagents' FilesystemMiddleware and stripped before the model sees
# them (there is no sandbox, so ``execute`` would error anyway).
_EXCLUDED_TOOLS = frozenset({"execute", "write_file", "edit_file"})

CHAT_PROMPT = """You are a code-review chat assistant. You help the author and reviewers \
understand one GitHub pull request: `{repo_owner}/{repo_name}` #{pr_number}.

You have NO sandbox and cannot run code, execute tests, commit, or open PRs. You \
reason from the PR's diff, the published review findings, and read-only access to \
the repository.

Context already loaded as virtual files (use `read_file`, `ls`, `grep`):
- `/pr/overview.md` — title, description, author, branches, head commit, change stats.
- `/pr/diff.patch` — the unified diff under review.
- `/pr/findings.md` — the reviewer's published findings, rendered for reading.

Tools:
- `read_repo_file(path, ref)` — read any repo file/dir at a commit (defaults to the \
PR head). Use it to inspect callers, definitions, and neighboring code beyond the diff.
- `search_repo_code(query)` — find a symbol or phrase across the repository.
- `list_review_findings(status_filter)` — the live findings (open/resolved/dismissed) \
with severity, confidence, and resolution notes.
- `web_search`, `fetch_url` — for external docs or standards.

Guidance:
- Be concrete and cite specific files and line numbers from the diff.
- Ground claims about the review in the actual findings; don't invent issues.
- When you propose a change, describe it precisely — you cannot apply it yourself.
- Keep answers focused and skimmable. Match the depth of the question.
"""


async def _cached_gateway_enabled() -> bool:
    return await ttl_cache.cached(
        f"team:gateway-enabled:{id(get_effective_gateway_enabled)}",
        60,
        get_effective_gateway_enabled,
    )


async def _cached_team_chat_model() -> tuple[str, str]:
    return await ttl_cache.cached(
        f"team-default-model:chat:{id(get_team_default_model)}",
        60,
        lambda: get_team_default_model("chat"),
    )


class PrepareChatRunMiddleware(BasePrepareRunMiddleware):
    def __init__(self, *, config: RunnableConfig) -> None:
        self._config = config

    async def _prepare(self, state: dict, runtime: object) -> dict:  # noqa: ARG002
        configurable = self._config["configurable"]
        repo_owner = str(configurable.get("chat_repo_owner") or "")
        repo_name = str(configurable.get("chat_repo_name") or "")
        pr_number = configurable.get("chat_pr_number")
        token = await get_github_app_installation_token(
            repositories=[repo_name] if repo_name else None
        )
        if isinstance(token, str) and token:
            configurable["chat_github_token"] = token
        return {
            "rendered_system_prompt": CHAT_PROMPT.format(
                repo_owner=repo_owner or "<owner>",
                repo_name=repo_name or "<repo>",
                pr_number=pr_number if isinstance(pr_number, int) else "?",
            )
        }


async def _resolve_chat_model(configurable: dict) -> tuple[str, str]:
    model_id = configurable.get("chat_model_id")
    effort = configurable.get("chat_effort")
    if (
        isinstance(model_id, str)
        and model_id in SUPPORTED_MODEL_IDS
        and isinstance(effort, str)
        and model_supports_effort(model_id, effort)
    ):
        return model_id, effort
    # Team review-chat default, which itself inherits the Agent default if unset.
    return await _cached_team_chat_model()


async def get_chat_agent(config: RunnableConfig) -> Pregel:
    """Get a read-only PR chat agent. No sandbox; PR context comes via config."""
    thread_id = config["configurable"].get("thread_id")
    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    if thread_id is None or not graph_loaded_for_execution(config):
        return create_deep_agent(system_prompt="", tools=[]).with_config(config)

    configurable = config["configurable"]
    model_id, effort = await _resolve_chat_model(configurable)
    use_gateway = await _cached_gateway_enabled()
    model_kwargs = provider_model_kwargs(
        model_id,
        effort,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
        openai_reasoning_default=DEFAULT_LLM_REASONING,
    )

    return create_deep_agent(
        model=make_model(model_id, use_gateway=use_gateway, **model_kwargs),
        system_prompt="",
        tools=[
            read_repo_file,
            search_repo_code,
            list_review_findings,
            web_search,
            fetch_url,
        ],
        middleware=[
            PrepareChatRunMiddleware(config=config),
            SanitizeToolInputsMiddleware(),
            ModelCallLimitMiddleware(run_limit=CHAT_MODEL_CALL_LIMIT, exit_behavior="end"),
            ToolErrorMiddleware(),
            ExcludeToolsMiddleware(excluded=_EXCLUDED_TOOLS),
            SanitizeFireworksMessagesMiddleware(),
            SanitizeThinkingBlocksMiddleware(),
        ],
    ).with_config(config)


traced_chat_agent = traced_graph_factory(get_chat_agent, AGENT_TRACING_PROJECT)
