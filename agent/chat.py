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

from agent.dashboard.options import (
    SUPPORTED_MODEL_IDS,
    canonical_model_pair,
    gate_fable_model,
    model_supports_effort,
)
from agent.dashboard.team_settings import (
    get_effective_gateway_enabled,
    get_team_default_model,
    get_team_fable_enabled,
)
from agent.github.app import get_github_app_installation_token
from agent.middleware import (
    BasePrepareRunMiddleware,
    ExcludeToolsMiddleware,
    ModelCallTimeoutMiddleware,
    SanitizeFireworksMessagesMiddleware,
    SanitizeOpenAIResponsesMiddleware,
    SanitizeThinkingBlocksMiddleware,
    SanitizeToolInputsMiddleware,
    ToolErrorMiddleware,
)
from agent.middleware.prepare_run import PrepareRunState
from agent.run_config import RunConfig
from agent.runtime import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_RECURSION_LIMIT,
    graph_loaded_for_execution,
)
from agent.tools import (
    fetch_url,
    list_review_findings,
    read_repo_file,
    search_repo_code,
    web_search,
)
from agent.utils import ttl_cache
from agent.utils.deferred_model import make_deferred_error_model
from agent.utils.model import DEFAULT_LLM_REASONING, make_model, provider_model_kwargs

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
- If repository access fails, disclose it and qualify claims that require unread source.
- When you propose a change, describe it precisely — you cannot apply it yourself.
- Keep answers focused and skimmable. Match the depth of the question.
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
        cfg = RunConfig.from_config(self._config)
        return {
            "prepare_run_id": cfg.prepare_run_id,
            "repo_owner": cfg.chat_repo_owner,
            "repo_name": cfg.chat_repo_name,
            "pr_number": cfg.chat_pr_number,
        }

    async def _prepare(self, state: PrepareRunState, runtime: Runtime) -> dict[str, Any]:  # noqa: ARG002
        configurable = self._config.get("configurable") or {}
        cfg = RunConfig.parse(configurable)
        repo_name = cfg.chat_repo_name or ""
        token = await get_github_app_installation_token(
            repositories=[repo_name] if repo_name else None,
            owner=cfg.chat_repo_owner or None,
            repo=repo_name or None,
        )
        if isinstance(token, str) and token:
            configurable["chat_github_token"] = token
        return {
            "rendered_system_prompt": CHAT_PROMPT.format(
                repo_owner=cfg.chat_repo_owner or "<owner>",
                repo_name=repo_name or "<repo>",
                pr_number=cfg.chat_pr_number if cfg.chat_pr_number is not None else "?",
            )
        }


async def _resolve_chat_model(cfg: RunConfig) -> tuple[str, str]:
    model_id = cfg.chat_model_id
    effort = cfg.chat_effort
    if (
        model_id is not None
        and model_id in SUPPORTED_MODEL_IDS
        and effort is not None
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
    cfg = RunConfig.parse(configurable)

    if cfg.thread_id is None or not graph_loaded_for_execution(config):
        return create_deep_agent(system_prompt="", tools=[]).with_config(config)

    model_id, effort = await _resolve_chat_model(cfg)
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


# langgraph.json entrypoint. Runs trace into LANGSMITH_PROJECT like everything else.
traced_chat_agent = get_chat_agent
