"""Main entry point and graph factory for the Open SWE agent.

Resolves the model, ensures one sandbox per thread (simplified
get-or-create-then-reconnect, no cross-process ``__creating__`` sentinel),
builds the curated tool list plus optional integrations, and wires the
middleware stack. All per-thread state lives in the sandbox + thread metadata;
the agent itself is stateless.
"""
# ruff: noqa: E402

import logging
import os
import warnings
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Literal, cast

logger = logging.getLogger(__name__)

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph.runtime import Runtime
from langgraph_sdk import get_client

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")

import asyncio

# Suppress Pydantic v1 compatibility warnings from langchain on Python 3.14+
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from deepagents import create_deep_agent
from deepagents.backends import LangSmithSandbox
from deepagents.backends.protocol import SandboxBackendProtocol
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolRetryMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langsmith.sandbox import SandboxClientError

from .dashboard.admin import is_observability_authorized
from .dashboard.agent_overrides import (
    load_profile,
    normalize_profile_overrides,
    normalize_profile_subagent_overrides,
    profile_create_prs,
    resolve_github_login,
)
from .dashboard.agent_usage import record_agent_thread_usage
from .dashboard.options import (
    SUPPORTED_MODEL_IDS,
    gate_fable_model,
    model_supports_effort,
)
from .dashboard.repo_snapshots import resolve_repo_snapshot_id
from .dashboard.team_settings import (
    get_effective_gateway_enabled,
    get_team_default_model_pair,
    get_team_default_repo,
    get_team_fable_enabled,
)
from .dashboard.user_mappings import email_for_login
from .integrations.corridor_mcp import load_corridor_tools
from .integrations.currents_tools import load_currents_tools
from .integrations.datadog_mcp import load_datadog_tools
from .integrations.langsmith import _configure_github_proxy, get_async_sandbox_client
from .integrations.langsmith_tools import load_langsmith_tools
from .integrations.notion_mcp import load_notion_tools
from .integrations.stagehand_browser import load_browser_tools
from .middleware import (
    BasePrepareRunMiddleware,
    ModelFallbackMiddleware,
    PlanModeMiddleware,
    PullRequestCreationGuardMiddleware,
    SanitizeFireworksMessagesMiddleware,
    SanitizeThinkingBlocksMiddleware,
    SanitizeToolInputsMiddleware,
    SlackAssistantStatusMiddleware,
    SubdirAgentsReadMiddleware,
    TimeoutWrapupMiddleware,
    ToolArtifactMiddleware,
    ToolErrorMiddleware,
    check_message_queue_before_model,
    notify_step_limit_reached,
    refresh_github_proxy_before_model,
    task_on_failure,
    task_retry_on,
)
from .middleware.prepare_run import PrepareRunState
from .prompt import construct_system_prompt
from .runtime.constants import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_RECURSION_LIMIT,
    MODEL_CALL_RECURSION_LIMIT,
)
from .runtime.constants import (
    DEFAULT_LLM_MODEL_ID as DEFAULT_LLM_MODEL_ID,
)
from .runtime.execution import graph_loaded_for_execution
from .tools import (
    approve_plan,
    enter_plan_mode,
    fetch_url,
    http_request,
    linear_comment,
    linear_create_issue,
    linear_delete_issue,
    linear_get_issue,
    linear_get_issue_comments,
    linear_list_teams,
    linear_search_issues,
    linear_update_issue,
    open_pull_request,
    report_platform_issue,
    request_pr_review,
    save_plan,
    schedule_thread_wakeup,
    slack_add_reaction,
    slack_read_thread_messages,
    slack_start_new_thread,
    slack_thread_reply,
    web_search,
)
from .utils import ttl_cache
from .utils.auth import resolve_github_token
from .utils.authorship import (
    OPEN_SWE_BOT_EMAIL,
    OPEN_SWE_BOT_NAME,
    resolve_triggering_user_identity,
)
from .utils.dashboard_links import dashboard_plan_url, dashboard_thread_url
from .utils.deferred_model import make_deferred_error_model
from .utils.github_app import get_github_app_installation_token_with_expiry
from .utils.github_proxy import record_proxy_token_expiry
from .utils.json_types import as_json_object
from .utils.model import (
    DEFAULT_LLM_REASONING,
    ModelKwargs,
    fallback_model_id_for,
    make_model,
    provider_model_kwargs,
)
from .utils.sandbox import create_sandbox
from .utils.sandbox_paths import aresolve_sandbox_work_dir
from .utils.sandbox_state import (
    SANDBOX_BACKENDS,
    get_or_create_sandbox_backend_proxy,
    get_sandbox_id_from_metadata,
    set_sandbox_backend,
    unwrap_sandbox_backend,
)
from .utils.tracing import AGENT_TRACING_PROJECT, traced_graph_factory

client = get_client()

DEFAULT_TOOL_LOADER_TIMEOUT_SECONDS = 5.0


def _tool_loader_timeout_seconds() -> float:
    raw_timeout = os.environ.get("TOOL_LOADER_TIMEOUT_SECONDS")
    if not raw_timeout:
        return DEFAULT_TOOL_LOADER_TIMEOUT_SECONDS
    try:
        timeout = float(raw_timeout)
    except ValueError:
        logger.warning("Invalid TOOL_LOADER_TIMEOUT_SECONDS=%r; using default", raw_timeout)
        return DEFAULT_TOOL_LOADER_TIMEOUT_SECONDS
    if timeout <= 0:
        logger.warning("TOOL_LOADER_TIMEOUT_SECONDS must be positive; using default")
        return DEFAULT_TOOL_LOADER_TIMEOUT_SECONDS
    return timeout


async def _resolve_prompt_default_repo(configurable: dict[str, Any]) -> dict[str, str] | None:
    repo_config = configurable.get("repo")
    if isinstance(repo_config, dict):
        owner = repo_config.get("owner")
        name = repo_config.get("name")
        if isinstance(owner, str) and isinstance(name, str):
            return {"owner": owner, "name": name}

    if configurable.get("repo_explicitly_none") is True:
        return None

    try:
        return await get_team_default_repo()
    except Exception:
        logger.debug("Failed to load team default repo for prompt", exc_info=True)
        return None


async def _resolve_repo_custom_instructions(
    default_repo: dict[str, str] | None,
) -> str | None:
    """Load per-repo custom agent instructions for the resolved default repo."""
    if not default_repo or not default_repo.get("owner") or not default_repo.get("name"):
        return None
    try:
        from .dashboard.agent_instructions import get_repo_agent_instructions

        return await get_repo_agent_instructions(default_repo["owner"], default_repo["name"])
    except Exception:
        logger.debug("Failed to load repo custom agent instructions", exc_info=True)
        return None


async def _start_langsmith_sandbox_if_needed(sandbox_backend: SandboxBackendProtocol) -> None:
    """Start a LangSmith sandbox before operations that require it to be running."""
    if os.getenv("SANDBOX_TYPE", "langsmith") != "langsmith":
        return
    current_backend = unwrap_sandbox_backend(sandbox_backend)
    if not isinstance(current_backend, LangSmithSandbox):
        return

    name = current_backend.id
    async with get_async_sandbox_client() as client:
        status = await client.get_sandbox_status(name)
        status_name = getattr(status, "status", status)
        status_name = getattr(status_name, "value", status_name)
        status_text = str(status_name or "").lower()
        if status_text in {"running", "ready"}:
            return

        logger.info(
            "Starting LangSmith sandbox %s before proxy refresh (status=%s)",
            name,
            status_text or "unknown",
        )
        await client.start_sandbox(name)


async def _resolve_proxy_token(
    github_proxy_token: str | None,
) -> tuple[str | None, str | None, None]:
    """Resolve the proxy token and its expiry."""
    if github_proxy_token:
        return github_proxy_token, None, None
    token, expires_at = await get_github_app_installation_token_with_expiry()
    return token, expires_at, None


async def _resolve_snapshot_id_for_repo(repo: dict[str, str] | None) -> str | None:
    """Resolve a repo's ready snapshot id; ``None`` falls back to the default.

    Never raises: any failure resolves to ``None`` so sandbox creation falls
    back to the configured ``DEFAULT_SANDBOX_SNAPSHOT_ID``.
    """
    if not repo:
        return None
    try:
        return await resolve_repo_snapshot_id(repo.get("owner"), repo.get("name"))
    except Exception:  # noqa: BLE001
        logger.debug("Failed to resolve repo-scoped snapshot", exc_info=True)
        return None


async def _create_sandbox_with_proxy(
    github_proxy_token: str | None = None,
    *,
    thread_id: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    repo: dict[str, str] | None = None,
) -> SandboxBackendProtocol:
    """Create a new sandbox with GitHub proxy auth configured."""
    snapshot_id = await _resolve_snapshot_id_for_repo(repo)
    sandbox_backend = await create_sandbox(snapshot_id=snapshot_id)

    sandbox_type = os.getenv("SANDBOX_TYPE", "langsmith")
    if sandbox_type == "langsmith":
        token, expires_at, permissions = await _resolve_proxy_token(github_proxy_token)
        if not token:
            msg = "Cannot configure proxy: GitHub App installation token is unavailable"
            logger.error(msg)
            raise ValueError(msg)
        await _start_langsmith_sandbox_if_needed(sandbox_backend)
        await _configure_github_proxy(sandbox_backend.id, token)
        record_proxy_token_expiry(
            thread_id,
            expires_at,
            repositories=github_proxy_repositories,
            permissions=permissions,
        )

    return sandbox_backend


async def _refresh_github_proxy(
    sandbox_backend: SandboxBackendProtocol,
    github_proxy_token: str | None = None,
    *,
    thread_id: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
) -> None:
    """Refresh GitHub proxy credentials for reused LangSmith sandboxes."""
    if os.getenv("SANDBOX_TYPE", "langsmith") != "langsmith":
        return

    token, expires_at, permissions = await _resolve_proxy_token(github_proxy_token)
    if not token:
        logger.warning(
            "Skipping GitHub proxy refresh for sandbox %s: installation token unavailable",
            sandbox_backend.id,
        )
        return

    current_backend = unwrap_sandbox_backend(sandbox_backend)
    await _start_langsmith_sandbox_if_needed(current_backend)
    await _configure_github_proxy(current_backend.id, token)
    record_proxy_token_expiry(
        thread_id,
        expires_at,
        repositories=github_proxy_repositories,
        permissions=permissions,
    )


async def _refresh_github_proxy_or_recreate(
    sandbox_backend: SandboxBackendProtocol,
    thread_id: str,
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    repo: dict[str, str] | None = None,
) -> SandboxBackendProtocol:
    """Refresh proxy credentials, recreating stale LangSmith sandboxes on failure."""
    try:
        await _refresh_github_proxy(
            sandbox_backend,
            github_proxy_token,
            thread_id=thread_id,
            github_proxy_repositories=github_proxy_repositories,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Failed to refresh GitHub proxy for sandbox %s on thread %s, recreating sandbox",
            sandbox_backend.id,
            thread_id,
            exc_info=True,
        )
        return await _recreate_sandbox(
            thread_id,
            github_proxy_token=github_proxy_token,
            github_proxy_repositories=github_proxy_repositories,
            repo=repo,
        )
    return sandbox_backend


async def _configure_git_identity(sandbox_backend: SandboxBackendProtocol) -> None:
    await asyncio.to_thread(
        sandbox_backend.execute,
        f"git config --global user.name '{OPEN_SWE_BOT_NAME}' && "
        f"git config --global user.email '{OPEN_SWE_BOT_EMAIL}'",
    )


async def _recreate_sandbox(
    thread_id: str,
    *,
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    repo: dict[str, str] | None = None,
) -> SandboxBackendProtocol:
    """Create a fresh sandbox (with proxy auth) after a connection failure.

    The agent is responsible for cloning repos via tools.
    """
    return set_sandbox_backend(
        thread_id,
        await _create_sandbox_with_proxy(
            github_proxy_token,
            thread_id=thread_id,
            github_proxy_repositories=github_proxy_repositories,
            repo=repo,
        ),
    )


async def check_or_recreate_sandbox(
    sandbox_backend: SandboxBackendProtocol,
    thread_id: str,
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    repo: dict[str, str] | None = None,
) -> SandboxBackendProtocol:
    """Check if a cached sandbox is reachable; recreate it if not.

    Pings the sandbox with a lightweight command. If the sandbox is
    unreachable (SandboxClientError), it is torn down and a fresh one
    is created via _recreate_sandbox.

    Returns the original backend if healthy, or a new one if recreated.
    """
    try:
        await asyncio.to_thread(sandbox_backend.execute, "echo ok")
    except SandboxClientError:
        logger.warning(
            "Cached sandbox is no longer reachable for thread %s, recreating",
            thread_id,
        )
        sandbox_backend = await _recreate_sandbox(
            thread_id,
            github_proxy_token=github_proxy_token,
            github_proxy_repositories=github_proxy_repositories,
            repo=repo,
        )
    return sandbox_backend


async def ensure_sandbox_for_thread(
    thread_id: str,
    *,
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    repo: dict[str, str] | None = None,
) -> SandboxBackendProtocol:
    """Get-or-create a healthy sandbox bound to ``thread_id``.

    Three cases (dispatch uses ``multitask_strategy="interrupt"``, so a thread
    never provisions two sandboxes concurrently — no cross-process sentinel is
    needed):

    1. Cached in memory -> ping; recreate on ``SandboxClientError``; refresh proxy.
    2. Metadata has an id -> reconnect; recreate on failure; refresh proxy.
    3. No sandbox at all -> create one and persist the id.

    For LangSmith sandboxes, also refreshes the GitHub App proxy auth. When
    ``repo`` has a ``ready`` repo-scoped snapshot, newly created sandboxes boot
    from it; otherwise the configured ``DEFAULT_SANDBOX_SNAPSHOT_ID`` is used.
    Re-applies git identity every run because reused/reconnected sandboxes can
    lose their ``--global`` config, and Vercel preview deploys reject commits
    whose author email can't be resolved to a GitHub account.
    """
    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if sandbox_backend is not None and not sandbox_backend.has_backend:
        sandbox_backend = None
    sandbox_id = await get_sandbox_id_from_metadata(thread_id)

    if sandbox_backend:
        logger.info("Using cached sandbox backend for thread %s", thread_id)
        original_sandbox_id = sandbox_backend.id
        sandbox_backend = await check_or_recreate_sandbox(
            sandbox_backend, thread_id, github_proxy_token, github_proxy_repositories, repo
        )
        if sandbox_backend.id == original_sandbox_id:
            sandbox_backend = await _refresh_github_proxy_or_recreate(
                sandbox_backend, thread_id, github_proxy_token, github_proxy_repositories, repo
            )
    elif sandbox_id is None:
        logger.info("Creating new sandbox for thread %s", thread_id)
        sandbox_backend = await _create_sandbox_with_proxy(
            github_proxy_token,
            thread_id=thread_id,
            github_proxy_repositories=github_proxy_repositories,
            repo=repo,
        )
        logger.info("Sandbox created: %s", sandbox_backend.id)
    else:
        logger.info("Connecting to existing sandbox %s", sandbox_id)
        try:
            sandbox_backend = await create_sandbox(sandbox_id)
        except Exception:
            logger.warning("Failed to connect to existing sandbox %s, creating new one", sandbox_id)
            sandbox_backend = await _create_sandbox_with_proxy(
                github_proxy_token,
                thread_id=thread_id,
                github_proxy_repositories=github_proxy_repositories,
                repo=repo,
            )
        else:
            original_sandbox_id = sandbox_backend.id
            sandbox_backend = await check_or_recreate_sandbox(
                sandbox_backend, thread_id, github_proxy_token, github_proxy_repositories, repo
            )
            if sandbox_backend.id == original_sandbox_id:
                sandbox_backend = await _refresh_github_proxy_or_recreate(
                    sandbox_backend, thread_id, github_proxy_token, github_proxy_repositories, repo
                )

    sandbox_backend = set_sandbox_backend(thread_id, sandbox_backend)

    if sandbox_id != sandbox_backend.id:
        await client.threads.update(
            thread_id=thread_id, metadata={"sandbox_id": sandbox_backend.id}
        )

    await _configure_git_identity(sandbox_backend)

    return sandbox_backend


# Mutating external tools hidden from the model while plan mode is active so it
# can only research and propose a plan. File edit tools stay available so the
# agent can draft and revise a plan under `/workspace/plans/`; prompt guidance
# restricts them to that plan file outside cloned repositories. `execute` stays
# available; plan-mode shell discipline (no mutating commands) is instructed via
# the system prompt rather than enforced. `http_request` is excluded because it
# can POST/PUT/PATCH/DELETE to external services — read-only web research goes
# through `web_search` / `fetch_url`. `task` is excluded because the
# general-purpose subagent is built with its own tools and does not inherit this
# exclusion, so delegating to it would bypass the read-only intent.
PLAN_MODE_EXCLUDED_TOOLS: frozenset[str] = frozenset(
    {
        "task",
        "http_request",
        "open_pull_request",
        "request_pr_review",
        "slack_start_new_thread",
        "linear_create_issue",
        "linear_update_issue",
        "linear_delete_issue",
    }
)


def _general_purpose_subagent(model: BaseChatModel) -> SubAgent:
    return {
        "name": GENERAL_PURPOSE_SUBAGENT["name"],
        "description": GENERAL_PURPOSE_SUBAGENT["description"],
        "system_prompt": GENERAL_PURPOSE_SUBAGENT["system_prompt"],
        "model": model,
    }


BROWSER_SUBAGENT_DESCRIPTION = (
    "Drives a real browser (Stagehand, running locally or on Browserbase) to "
    "accomplish tasks that require interacting with live web pages: logging "
    "into dashboards, clicking through flows, filling forms, reading "
    "JS-rendered content, reproducing UI bugs, and extracting structured data. "
    "Prefer the `fetch_url` tool for static page reads; delegate here only when "
    "the task needs interaction or JavaScript-rendered content."
)

BROWSER_SUBAGENT_SYSTEM_PROMPT = """You are a browser automation specialist. You control a real Chromium \
browser via Stagehand tools.

Workflow:
1. Call `browser_navigate` to open the browser and go to the starting URL.
2. Use `browser_observe` to find actionable elements before acting when the \
page is unfamiliar.
3. Use `browser_act` for clicks/typing/navigation with concise \
natural-language instructions (one action per call).
4. Use `browser_extract` to pull the specific data the caller asked for, \
passing a JSON schema when you need a precise shape.
5. Always call `browser_close` when finished to release the session.

Guidance:
- Take one concrete step at a time and verify the result before the next.
- Keep instructions specific and grounded in what `browser_observe`/\
`browser_extract` returned.
- Do not exfiltrate credentials or secrets. Only act on the task you were \
delegated.
- Return a concise summary of what you did and the data you extracted; include \
the session replay URL if one was returned."""


def _browser_subagent(model: BaseChatModel, tools: list[Any]) -> SubAgent:
    return {
        "name": "browser",
        "description": BROWSER_SUBAGENT_DESCRIPTION,
        "system_prompt": BROWSER_SUBAGENT_SYSTEM_PROMPT,
        "tools": tools,
        "model": model,
    }


def _get_cached_sandbox_backend(
    thread_id: str,
    *,
    reconnect: Callable[[], Awaitable[SandboxBackendProtocol]] | None = None,
) -> SandboxBackendProtocol:
    return get_or_create_sandbox_backend_proxy(thread_id, reconnect=reconnect)


get_cached_sandbox_backend = _get_cached_sandbox_backend
configure_git_identity = _configure_git_identity
recreate_sandbox = _recreate_sandbox


async def _observability_authorized(config: RunnableConfig, profile_login: str | None) -> bool:
    """Whether the triggering user may use the team observability tools.

    Gates on admin / explicitly-authorized emails so prompt-injected runs from
    untrusted contributors cannot reach the team's Datadog/LangSmith data.
    """
    configurable = (config or {}).get("configurable") or {}
    slack_thread = configurable.get("slack_thread") or {}
    config_login = configurable.get("github_login")
    candidate_login = profile_login or (config_login if isinstance(config_login, str) else None)
    candidate_emails = [
        configurable.get("user_email"),
        slack_thread.get("triggering_user_email"),
    ]
    if any(is_observability_authorized(email, login=candidate_login) for email in candidate_emails):
        return True
    return is_observability_authorized(
        await email_for_login(candidate_login), login=candidate_login
    )


async def _cached_tool_loader(key: str, ttl_seconds: float, loader: Any) -> list[Any]:
    async def load_with_timeout() -> list[Any]:
        return await asyncio.wait_for(loader(), timeout=_tool_loader_timeout_seconds())

    try:
        return await ttl_cache.cached_stale_while_revalidate(key, ttl_seconds, load_with_timeout)
    except TimeoutError:
        logger.warning("Timed out loading cached tools for %s", key, exc_info=True)
        return []
    except Exception:
        logger.warning("Failed to load cached tools for %s", key, exc_info=True)
        return []


async def _load_observability_tools(authorized: bool) -> list[Any]:
    """Datadog (MCP) + LangSmith read tools when the team has connected them.

    Credentials live server-side in team settings; the sandbox never holds them.
    Only loaded for authorized (admin / allow-listed) triggering users so an
    untrusted run cannot exfiltrate team observability data. Failures degrade to
    no tools so the agent still starts.
    """
    if not authorized:
        return []
    datadog_tools, langsmith_tools = await asyncio.gather(
        _cached_tool_loader(f"tools:datadog:{id(load_datadog_tools)}", 600, load_datadog_tools),
        _cached_tool_loader(
            f"tools:langsmith:{id(load_langsmith_tools)}", 600, load_langsmith_tools
        ),
    )
    return [*datadog_tools, *langsmith_tools]


async def _load_corridor_mcp_tools() -> list[Any]:
    """Corridor MCP tools when the deployment environment has configured them."""
    return await _cached_tool_loader(
        f"tools:corridor:{id(load_corridor_tools)}", 600, load_corridor_tools
    )


async def _cached_team_default_model_pair(kind: Literal["agent", "reviewer"]):
    return await ttl_cache.cached(
        f"team-default-model-pair:{kind}:{id(get_team_default_model_pair)}",
        60,
        lambda: get_team_default_model_pair(kind),
    )


async def _cached_gateway_enabled() -> bool:
    return await ttl_cache.cached(
        f"team:gateway-enabled:{id(get_effective_gateway_enabled)}",
        60,
        get_effective_gateway_enabled,
    )


async def _cached_fable_enabled() -> bool:
    return await ttl_cache.cached(
        f"team:fable-enabled:{id(get_team_fable_enabled)}",
        60,
        get_team_fable_enabled,
    )


async def _cached_profile(profile_login: str | None):
    if not profile_login:
        return None
    return await ttl_cache.cached(
        f"profile:{profile_login}:{id(load_profile)}", 30, lambda: load_profile(profile_login)
    )


def _make_model_or_defer(
    model_id: str,
    *,
    use_gateway: bool,
    **kwargs: Any,
) -> BaseChatModel:
    try:
        return make_model(model_id, use_gateway=use_gateway, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("Deferring model setup failure for %s", model_id, exc_info=True)
        return make_deferred_error_model(e, model_id=model_id)


class PrepareAgentRunMiddleware(BasePrepareRunMiddleware):
    def __init__(
        self,
        *,
        thread_id: str,
        config: RunnableConfig,
        profile_login: str | None,
        model_id: str,
        effort: str | None,
        source: str,
        user_email: str,
        linear_project_id: str,
        linear_issue_number: str,
        create_prs: bool,
        plan_mode: bool,
        corridor_enabled: bool,
    ) -> None:
        self._thread_id = thread_id
        self._config = config
        self._profile_login = profile_login
        self._model_id = model_id
        self._effort = effort
        self._source = source
        self._user_email = user_email
        self._linear_project_id = linear_project_id
        self._linear_issue_number = linear_issue_number
        self._create_prs = create_prs
        self._plan_mode = plan_mode
        self._corridor_enabled = corridor_enabled

    def _prepare_config_fingerprint(self) -> Any:
        configurable = (self._config or {}).get("configurable") or {}
        return {
            "prepare_run_id": configurable.get("prepare_run_id"),
            "thread_id": self._thread_id,
            "source": self._source,
            "repo": configurable.get("repo"),
            "plan_mode": self._plan_mode,
            "model": self._model_id,
            "effort": self._effort,
        }

    async def _prepare(self, state: PrepareRunState, runtime: Runtime) -> dict[str, Any]:  # noqa: ARG002
        github_token, _expires_at = await resolve_github_token(self._config, self._thread_id)
        configurable = (self._config or {}).get("configurable") or {}
        prompt_default_repo = await _resolve_prompt_default_repo(configurable)
        triggering_user_identity_task = asyncio.create_task(
            asyncio.to_thread(
                resolve_triggering_user_identity, as_json_object(self._config), github_token
            )
        )
        sandbox_task = asyncio.create_task(
            ensure_sandbox_for_thread(self._thread_id, repo=prompt_default_repo)
        )
        triggering_user_identity, sandbox_backend = await asyncio.gather(
            triggering_user_identity_task,
            sandbox_task,
        )
        del github_token
        work_dir = await aresolve_sandbox_work_dir(sandbox_backend)
        repo_custom_instructions = await _resolve_repo_custom_instructions(prompt_default_repo)

        try:
            await client.threads.update(
                thread_id=self._thread_id,
                metadata={
                    "agent_kind": "agent",
                    "model": self._model_id,
                    "effort": self._effort,
                    "source": self._source,
                    "plan_mode": self._plan_mode,
                },
            )
            await record_agent_thread_usage(
                thread_id=self._thread_id,
                github_login=self._profile_login,
                user_email=self._user_email,
                model_id=self._model_id,
                effort=self._effort,
                source=self._source,
            )
        except Exception:
            logger.debug(
                "Failed to record agent usage for thread %s", self._thread_id, exc_info=True
            )

        return {
            "work_dir": work_dir,
            "rendered_system_prompt": construct_system_prompt(
                working_dir=work_dir,
                linear_project_id=self._linear_project_id,
                linear_issue_number=self._linear_issue_number,
                triggering_user_identity=triggering_user_identity,
                create_prs=self._create_prs,
                default_repo=prompt_default_repo,
                plan_mode=self._plan_mode,
                plan_url=dashboard_plan_url(self._thread_id),
                repo_custom_instructions=repo_custom_instructions,
                thread_url=dashboard_thread_url(self._thread_id),
                corridor_enabled=self._corridor_enabled,
            ),
        }


async def get_agent(config: RunnableConfig) -> Pregel:
    """Get or create an agent with a sandbox for the given thread."""
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")

    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    if thread_id is None or not graph_loaded_for_execution(config):
        logger.info("No thread_id or not for execution, returning agent without sandbox")
        return create_deep_agent(
            system_prompt="",
            tools=[],
        ).with_config(config)

    profile_login = resolve_github_login(as_json_object(config))
    # Team/profile settings are accepted stale for a short TTL so graph factories
    # stay off the critical path during worker load and retry storms.
    team_defaults, use_gateway, profile, fable_enabled = await asyncio.gather(
        _cached_team_default_model_pair("agent"),
        _cached_gateway_enabled(),
        _cached_profile(profile_login),
        _cached_fable_enabled(),
    )

    linear_issue = as_json_object(configurable.get("linear_issue"))
    linear_project_id = linear_issue.get("linear_project_id", "")
    linear_issue_number = linear_issue.get("linear_issue_number", "")

    async def reconnect_backend(
        _thread_id: str = thread_id,
        _configurable: dict[str, Any] = configurable,
    ) -> SandboxBackendProtocol:
        prompt_default_repo = await _resolve_prompt_default_repo(_configurable)
        return await ensure_sandbox_for_thread(_thread_id, repo=prompt_default_repo)

    backend = _get_cached_sandbox_backend(thread_id, reconnect=reconnect_backend)

    (model_id, profile_effort), (subagent_model_id, subagent_effort) = team_defaults
    logger.info("Using team default agent model: model=%s effort=%s", model_id, profile_effort)

    if profile_login and profile:
        overridden_model, overridden_effort = normalize_profile_overrides(profile)
        if overridden_model:
            logger.info(
                "Applying dashboard profile override for %s: model=%s effort=%s",
                profile_login,
                overridden_model,
                overridden_effort,
            )
            model_id = overridden_model
            profile_effort = overridden_effort
            subagent_model_id = overridden_model
            subagent_effort = overridden_effort
        overridden_subagent_model, overridden_subagent_effort = (
            normalize_profile_subagent_overrides(profile)
        )
        if overridden_subagent_model:
            logger.info(
                "Applying dashboard profile subagent override for %s: model=%s effort=%s",
                profile_login,
                overridden_subagent_model,
                overridden_subagent_effort,
            )
            subagent_model_id = overridden_subagent_model
            subagent_effort = overridden_subagent_effort

    per_thread_model = configurable.get("agent_model_id")
    per_thread_effort = configurable.get("agent_effort")
    if (
        isinstance(per_thread_model, str)
        and per_thread_model in SUPPORTED_MODEL_IDS
        and isinstance(per_thread_effort, str)
        and model_supports_effort(per_thread_model, per_thread_effort)
    ):
        logger.info(
            "Applying per-thread model override: model=%s effort=%s",
            per_thread_model,
            per_thread_effort,
        )
        model_id = per_thread_model
        profile_effort = per_thread_effort
        subagent_model_id = per_thread_model
        subagent_effort = per_thread_effort

    always_create_prs = profile_create_prs(profile)
    if always_create_prs:
        logger.info("Always Create PRs enabled by profile for %s", profile_login)

    model_id, profile_effort = gate_fable_model(
        model_id, profile_effort, fable_enabled=fable_enabled
    )
    subagent_model_id, subagent_effort = gate_fable_model(
        subagent_model_id, subagent_effort, fable_enabled=fable_enabled
    )

    model_kwargs = provider_model_kwargs(
        model_id,
        profile_effort,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
    )
    subagent_model_kwargs = provider_model_kwargs(
        subagent_model_id,
        subagent_effort,
        max_tokens=DEFAULT_LLM_MAX_TOKENS,
    )

    fallback_model_id = os.environ.get("LLM_FALLBACK_MODEL_ID") or fallback_model_id_for(model_id)
    fallback_middleware: list[Any] = []
    if fallback_model_id and fallback_model_id != model_id:
        fallback_kwargs: ModelKwargs = {"max_tokens": DEFAULT_LLM_MAX_TOKENS}
        if fallback_model_id.startswith("openai:"):
            fallback_kwargs["reasoning"] = DEFAULT_LLM_REASONING
        fallback_middleware.append(
            ModelFallbackMiddleware(
                _make_model_or_defer(fallback_model_id, use_gateway=use_gateway, **fallback_kwargs)
            )
        )
        logger.info("Configured model fallback %s -> %s", model_id, fallback_model_id)

    source_value = configurable.get("source")
    source = source_value if isinstance(source_value, str) else "dashboard"
    user_email = configurable.get("user_email")
    user_email = user_email if isinstance(user_email, str) else ""

    # Plan mode is entered only when the model decides to (the `enter_plan_mode`
    # tool sets it in run state). The configurable value just carries that
    # decision across a thread's messages and the approve/reject follow-ups; a
    # fresh run with nothing set starts out of plan mode. Installed
    # unconditionally and state-aware: it also restricts tools after a mid-run
    # `enter_plan_mode` call, not just when plan mode is set up front.
    plan_mode = configurable.get("plan_mode") is True
    if plan_mode:
        logger.info("Plan mode enabled for thread %s", thread_id)
    plan_mode_middleware: list[Any] = [
        PlanModeMiddleware(excluded=PLAN_MODE_EXCLUDED_TOOLS, initial=plan_mode)
    ]

    observability_tools = await _load_observability_tools(
        await _observability_authorized(config, profile_login)
    )
    corridor_tools = await _load_corridor_mcp_tools()
    browser_tools = load_browser_tools()

    currents_tools: list[Any] = []
    notion_tools: list[Any] = []
    if profile_login:
        currents_tools, notion_tools = await asyncio.gather(
            _cached_tool_loader(
                f"tools:currents:{profile_login}:{id(load_currents_tools)}",
                300,
                lambda: load_currents_tools(profile_login),
            ),
            _cached_tool_loader(
                f"tools:notion:{profile_login}:{id(load_notion_tools)}",
                300,
                lambda: load_notion_tools(profile_login),
            ),
        )

    logger.info("Returning agent with sandbox for thread %s", thread_id)
    main_model = _make_model_or_defer(model_id, use_gateway=use_gateway, **model_kwargs)
    subagent_model = _make_model_or_defer(
        subagent_model_id,
        use_gateway=use_gateway,
        **subagent_model_kwargs,
    )
    return create_deep_agent(
        model=main_model,
        system_prompt="",
        tools=[
            http_request,
            fetch_url,
            web_search,
            approve_plan,
            enter_plan_mode,
            save_plan,
            linear_comment,
            linear_create_issue,
            linear_delete_issue,
            linear_get_issue,
            linear_get_issue_comments,
            linear_list_teams,
            linear_search_issues,
            linear_update_issue,
            open_pull_request,
            request_pr_review,
            report_platform_issue,
            schedule_thread_wakeup,
            slack_add_reaction,
            slack_read_thread_messages,
            slack_start_new_thread,
            slack_thread_reply,
            *corridor_tools,
            *observability_tools,
            *currents_tools,
            *notion_tools,
        ],
        subagents=[
            _general_purpose_subagent(subagent_model),
            *([_browser_subagent(subagent_model, browser_tools)] if browser_tools else []),
        ],
        backend=backend,
        middleware=cast(
            list[AgentMiddleware[Any, Any, Any]],
            [
                PrepareAgentRunMiddleware(
                    thread_id=thread_id,
                    config=config,
                    profile_login=profile_login,
                    model_id=model_id,
                    effort=profile_effort,
                    source=source,
                    user_email=user_email,
                    linear_project_id=linear_project_id,
                    linear_issue_number=linear_issue_number,
                    create_prs=always_create_prs,
                    plan_mode=plan_mode,
                    corridor_enabled=bool(corridor_tools),
                ),
                SanitizeToolInputsMiddleware(),
                ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"),
                ToolErrorMiddleware(),
                SubdirAgentsReadMiddleware(),
                ToolRetryMiddleware(
                    max_retries=2,
                    tools=["task"],
                    retry_on=task_retry_on,
                    on_failure=task_on_failure,
                    initial_delay=1.0,
                    max_delay=10.0,
                ),
                ToolArtifactMiddleware(),
                PullRequestCreationGuardMiddleware(),
                refresh_github_proxy_before_model,
                check_message_queue_before_model,
                SlackAssistantStatusMiddleware(),
                TimeoutWrapupMiddleware(),
                notify_step_limit_reached,
                *fallback_middleware,
                *plan_mode_middleware,
                SanitizeFireworksMessagesMiddleware(),
                SanitizeThinkingBlocksMiddleware(),
            ],
        ),
    ).with_config(config)


traced_agent = traced_graph_factory(get_agent, AGENT_TRACING_PROJECT)
