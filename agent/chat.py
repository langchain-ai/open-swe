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

import logging
import warnings
from typing import Any, cast

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph.runtime import Runtime

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from deepagents import create_deep_agent
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel

from .dashboard.options import (
    SUPPORTED_MODEL_IDS,
    canonical_model_pair,
    gate_fable_model,
    model_supports_effort,
)
from .dashboard.team_settings import (
    get_effective_gateway_enabled,
    get_team_default_model,
    get_team_fable_enabled,
)
from .middleware import (
    BasePrepareRunMiddleware,
    ExcludeToolsMiddleware,
    ModelCallTimeoutMiddleware,
    SanitizeFireworksMessagesMiddleware,
    SanitizeOpenAIResponsesMiddleware,
    SanitizeThinkingBlocksMiddleware,
    SanitizeToolInputsMiddleware,
    ToolErrorMiddleware,
)
from .middleware.prepare_run import PrepareRunState
from .runtime import (
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
from .utils.deferred_model import make_deferred_error_model
from .utils.github_app import get_github_app_installation_token
from .utils.model import DEFAULT_LLM_REASONING, make_model, provider_model_kwargs
from .utils.tracing import AGENT_TRACING_PROJECT, traced_graph_factory

logger = logging.getLogger(__name__)

CHAT_MODEL_CALL_LIMIT = 100

# Read-only: the chat agent never mutates files or runs shell commands. These are
# injected by deepagents' FilesystemMiddleware and stripped before the model sees
# them (there is no sandbox, so ``execute`` would error anyway).
_EXCLUDED_TOOLS = frozenset({"execute", "write_file", "edit_file", "delete"})


def _chat_general_purpose_subagent() -> SubAgent:
    # Deep Agents auto-adds a general-purpose subagent whose default
    # FilesystemMiddleware would expose write_file/edit_file/delete/execute.
    # Declaring the spec here suppresses that default and swaps in an
    # allowlisted FilesystemMiddleware so delegated work stays read-only.
    return {
        "name": GENERAL_PURPOSE_SUBAGENT["name"],
        "description": GENERAL_PURPOSE_SUBAGENT["description"],
        "system_prompt": GENERAL_PURPOSE_SUBAGENT["system_prompt"],
        "middleware": cast(
            list[AgentMiddleware[Any, Any, Any]],
            [
                FilesystemMiddleware(tools=["read_file", "ls", "glob", "grep"]),
                SanitizeOpenAIResponsesMiddleware(),
                ModelCallTimeoutMiddleware(),
            ],
        ),
    }


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
- Never issue an unqualified verdict such as "no breaking changes", "no security
  issues", or "no regressions" when a `read_repo_file` or `search_repo_code` call
  in this turn returned `success: false`; state the limitation in the answer.
- When you propose a change, describe it precisely — you cannot apply it yourself.
- Keep answers focused and skimmable. Match the depth of the question.
{repo_access_reminder}
"""


async def _cached_gateway_enabled() -> bool:
    return await ttl_cache.cached(
        "team:gateway-enabled",
        60,
        get_effective_gateway_enabled,
    )


async def _cached_team_chat_model() -> tuple[str, str]:
    return await ttl_cache.cached(
        "team-default-model:chat",
        60,
        lambda: get_team_default_model("chat"),
    )


def _make_model_or_defer(model_id: str, *, use_gateway: bool, **kwargs: Any) -> BaseChatModel:
    try:
        return make_model(model_id, use_gateway=use_gateway, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("Deferring chat model setup failure for %s", model_id, exc_info=True)
        return make_deferred_error_model(e, model_id=model_id)


class PrepareChatRunMiddleware(BasePrepareRunMiddleware):
    def __init__(self, *, config: RunnableConfig) -> None:
        self._config = config

    def _prepare_config_fingerprint(self) -> object:
        configurable = self._config.get("configurable", {})
        return {
            "prepare_run_id": configurable.get("prepare_run_id")
            if isinstance(configurable, dict)
            else None,
            "repo_owner": configurable.get("chat_repo_owner")
            if isinstance(configurable, dict)
            else None,
            "repo_name": configurable.get("chat_repo_name")
            if isinstance(configurable, dict)
            else None,
            "pr_number": configurable.get("chat_pr_number")
            if isinstance(configurable, dict)
            else None,
        }

    async def _prepare(self, state: PrepareRunState, runtime: Runtime) -> dict[str, Any]:  # noqa: ARG002
        configurable = self._config.get("configurable") or {}
        repo_owner = str(configurable.get("chat_repo_owner") or "")
        repo_name = str(configurable.get("chat_repo_name") or "")
        pr_number = configurable.get("chat_pr_number")
        token = await get_github_app_installation_token(
            repositories=[repo_name] if repo_name else None
        )
        token_available = isinstance(token, str) and bool(token.strip())
        if token_available:
            configurable["chat_github_token"] = token
        repo_access_reminder = ""
        if not token_available:
            repo_access_reminder = (
                "\nSystem reminder: repo-wide source inspection is unavailable this turn. "
                "Base conclusions only on `/pr/diff.patch`, `/pr/overview.md`, and "
                "`/pr/findings.md`. If the user asks a question that requires reading "
                "files outside the diff, say plainly that the file could not be "
                "retrieved rather than answering as if it had been read."
            )
        return {
            "rendered_system_prompt": CHAT_PROMPT.format(
                repo_owner=repo_owner or "<owner>",
                repo_name=repo_name or "<repo>",
                pr_number=pr_number if isinstance(pr_number, int) else "?",
                repo_access_reminder=repo_access_reminder,
            )
        }


async def _resolve_chat_model(configurable: dict[str, Any]) -> tuple[str, str]:
    model_id = configurable.get("chat_model_id")
    effort = configurable.get("chat_effort")
    if (
        isinstance(model_id, str)
        and model_id in SUPPORTED_MODEL_IDS
        and isinstance(effort, str)
        and model_supports_effort(model_id, effort)
    ):
        return model_id, effort
    canonical = canonical_model_pair(model_id, effort)
    if canonical is not None:
        return canonical
    # Team review-chat default, which itself inherits the Agent default if unset.
    return await _cached_team_chat_model()


async def get_chat_agent(config: RunnableConfig) -> Pregel:
    """Get a read-only PR chat agent. No sandbox; PR context comes via config."""
    config = config.copy()
    configurable = dict(config.get("configurable") or {})
    config["configurable"] = configurable
    config.setdefault("recursion_limit", DEFAULT_RECURSION_LIMIT)
    thread_id = configurable.get("thread_id")

    if thread_id is None or not graph_loaded_for_execution(config):
        return create_deep_agent(system_prompt="", tools=[]).with_config(config)

    model_id, effort = await _resolve_chat_model(configurable)
    model_id, effort = gate_fable_model(
        model_id, effort, fable_enabled=await get_team_fable_enabled()
    )
    use_gateway = await _cached_gateway_enabled()
    model_kwargs = provider_model_kwargs(
        model_id,
        effort,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
        openai_reasoning_default=DEFAULT_LLM_REASONING,
    )

    return create_deep_agent(
        model=_make_model_or_defer(model_id, use_gateway=use_gateway, **model_kwargs),
        system_prompt="",
        tools=[
            read_repo_file,
            search_repo_code,
            list_review_findings,
            web_search,
            fetch_url,
        ],
        subagents=[_chat_general_purpose_subagent()],
        middleware=cast(
            list[AgentMiddleware[Any, Any, Any]],
            [
                PrepareChatRunMiddleware(config=config),
                SanitizeToolInputsMiddleware(),
                ModelCallLimitMiddleware(run_limit=CHAT_MODEL_CALL_LIMIT, exit_behavior="end"),
                ToolErrorMiddleware(),
                ExcludeToolsMiddleware(excluded=_EXCLUDED_TOOLS),
                SanitizeFireworksMessagesMiddleware(),
                SanitizeOpenAIResponsesMiddleware(),
                SanitizeThinkingBlocksMiddleware(),
                ModelCallTimeoutMiddleware(),
            ],
        ),
    ).with_config(config)


traced_chat_agent = traced_graph_factory(get_chat_agent, AGENT_TRACING_PROJECT)
