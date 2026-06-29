"""Main entry point and CLI loop for Open SWE agent."""
# ruff: noqa: E402

# Suppress deprecation warnings from langchain_core (e.g., Pydantic V1 on Python 3.14+)
# ruff: noqa: E402
import logging
import os
import time
import warnings
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph_sdk import get_client

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")

import asyncio

# Suppress Pydantic v1 compatibility warnings from langchain on Python 3.14+
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from deepagents import create_deep_agent
from deepagents.backends import LangSmithSandbox
from deepagents.backends.protocol import SandboxBackendProtocol
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent
from langchain.agents.middleware import ModelCallLimitMiddleware
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
from .dashboard.options import DEFAULT_MODEL_ID, SUPPORTED_MODEL_IDS, model_supports_effort
from .dashboard.repo_snapshots import resolve_repo_snapshot_id
from .dashboard.team_settings import (
    get_team_default_model_pair,
    get_team_default_repo,
)
from .dashboard.user_mappings import email_for_login
from .integrations.corridor_mcp import load_corridor_tools
from .integrations.currents_tools import load_currents_tools
from .integrations.datadog_mcp import load_datadog_tools
from .integrations.langsmith import _configure_github_proxy
from .integrations.langsmith_tools import load_langsmith_tools
from .integrations.notion_mcp import load_notion_tools
from .middleware import (
    ModelFallbackMiddleware,
    PlanModeMiddleware,
    SandboxCircuitBreakerMiddleware,
    SanitizeThinkingBlocksMiddleware,
    SanitizeToolInputsMiddleware,
    SlackAssistantStatusMiddleware,
    ToolArtifactMiddleware,
    ToolErrorMiddleware,
    WorkflowPushGuardMiddleware,
    check_message_queue_before_model,
    ensure_no_empty_msg,
    notify_step_limit_reached,
    refresh_github_proxy_before_model,
)
from .prompt import construct_system_prompt
from .tools import (
    enter_plan_mode,
    fetch_url,
    http_request,
    linear_comment,
    linear_create_issue,
    linear_delete_issue,
    linear_get_issue,
    linear_get_issue_comments,
    linear_list_teams,
    linear_update_issue,
    open_pull_request,
    request_pr_review,
    save_plan,
    schedule_thread_wakeup,
    slack_read_thread_messages,
    slack_start_new_thread,
    slack_thread_reply,
    web_search,
)
from .utils.auth import resolve_github_token
from .utils.authorship import (
    OPEN_SWE_BOT_EMAIL,
    OPEN_SWE_BOT_NAME,
    resolve_triggering_user_identity,
)
from .utils.dashboard_links import dashboard_plan_url, dashboard_thread_url
from .utils.github_app import (
    BASE_RUNTIME_PROXY_TOKEN_PERMISSIONS,
    RUNTIME_PROXY_TOKEN_PERMISSIONS,
    PermissionMap,
    get_github_app_installation_token_with_expiry,
)
from .utils.github_proxy import record_proxy_token_expiry
from .utils.model import (
    DEFAULT_LLM_REASONING,
    ModelKwargs,
    fallback_model_id_for,
    make_model,
    provider_model_kwargs,
)
from .utils.sandbox import create_sandbox
from .utils.sandbox_paths import aresolve_sandbox_work_dir
from .utils.tracing import AGENT_TRACING_PROJECT, traced_graph_factory

client = get_client()

SANDBOX_CREATING = "__creating__"
SANDBOX_CREATION_TIMEOUT = 180
SANDBOX_POLL_INTERVAL = 1.0

from .utils.sandbox_state import (
    SANDBOX_BACKENDS,
    get_sandbox_id_from_metadata,
    set_sandbox_backend,
    unwrap_sandbox_backend,
)


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

    sandbox = current_backend._sandbox  # noqa: SLF001
    status = await asyncio.to_thread(sandbox._client.get_sandbox_status, sandbox.name)  # noqa: SLF001
    status_name = getattr(status, "status", status)
    status_name = getattr(status_name, "value", status_name)
    status_text = str(status_name or "").lower()
    if status_text in {"running", "ready"}:
        return

    logger.info(
        "Starting LangSmith sandbox %s before proxy refresh (status=%s)",
        current_backend.id,
        status_text or "unknown",
    )
    await asyncio.to_thread(sandbox.start)


async def _resolve_proxy_token(
    github_proxy_token: str | None,
    *,
    permissions: PermissionMap | None = None,
) -> tuple[str | None, str | None, PermissionMap | None]:
    """Resolve the proxy token, its expiry, and the effective permission scope."""
    if github_proxy_token:
        return github_proxy_token, None, None
    if permissions is not None:
        token, expires_at = await get_github_app_installation_token_with_expiry(
            permissions=permissions
        )
        return token, expires_at, permissions

    token, expires_at = await get_github_app_installation_token_with_expiry(
        permissions=RUNTIME_PROXY_TOKEN_PERMISSIONS,
        log_errors=False,
    )
    if token:
        return token, expires_at, RUNTIME_PROXY_TOKEN_PERMISSIONS

    logger.warning("Retrying GitHub proxy token mint without optional Actions read permission")
    token, expires_at = await get_github_app_installation_token_with_expiry(
        permissions=BASE_RUNTIME_PROXY_TOKEN_PERMISSIONS
    )
    return token, expires_at, BASE_RUNTIME_PROXY_TOKEN_PERMISSIONS if token else None


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
    sandbox_backend = await asyncio.to_thread(create_sandbox, snapshot_id=snapshot_id)

    sandbox_type = os.getenv("SANDBOX_TYPE", "langsmith")
    if sandbox_type == "langsmith":
        token, expires_at, permissions = await _resolve_proxy_token(github_proxy_token)
        if not token:
            msg = "Cannot configure proxy: GitHub App installation token is unavailable"
            logger.error(msg)
            raise ValueError(msg)
        await _start_langsmith_sandbox_if_needed(sandbox_backend)
        await asyncio.to_thread(_configure_github_proxy, sandbox_backend.id, token)
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
    await asyncio.to_thread(_configure_github_proxy, current_backend.id, token)
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
    """Recreate a sandbox after a connection failure.

    Sets the SANDBOX_CREATING sentinel and creates a fresh sandbox
    (with proxy auth configured), swapping the per-thread proxy target.
    The agent is responsible for cloning repos via tools.
    """
    await client.threads.update(thread_id=thread_id, metadata=_creating_metadata())
    try:
        sandbox_backend = set_sandbox_backend(
            thread_id,
            await _create_sandbox_with_proxy(
                github_proxy_token,
                thread_id=thread_id,
                github_proxy_repositories=github_proxy_repositories,
                repo=repo,
            ),
        )
    except Exception:
        logger.exception("Failed to recreate sandbox after connection failure")
        await client.threads.update(thread_id=thread_id, metadata=_RESET_METADATA)
        raise
    return sandbox_backend


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


def _creating_metadata() -> dict[str, Any]:
    """Metadata that claims the cross-process creation lock with a timestamp."""
    return {"sandbox_id": SANDBOX_CREATING, "sandbox_creating_at": time.time()}


_RESET_METADATA: dict[str, Any] = {"sandbox_id": None, "sandbox_creating_at": None}


async def _resolve_creating_sentinel(thread_id: str) -> str | None:
    """Resolve a ``__creating__`` sentinel seen with no cached backend.

    The sentinel is a cross-process lock: another worker may still be creating
    the sandbox. Poll live thread metadata until it resolves to a real id. Only
    when the sentinel is older than ``SANDBOX_CREATION_TIMEOUT`` (e.g. the
    creating worker was restarted) is it treated as stale: metadata is reset and
    ``None`` is returned so the caller creates a fresh sandbox. A sentinel with
    no timestamp (written before this field existed) is also treated as stale.
    """
    while True:
        thread = await client.threads.get(thread_id)
        metadata = thread.get("metadata", {}) if isinstance(thread, dict) else {}
        sandbox_id = metadata.get("sandbox_id") if isinstance(metadata, dict) else None

        if sandbox_id != SANDBOX_CREATING:
            return sandbox_id if isinstance(sandbox_id, str) else None

        creating_at = metadata.get("sandbox_creating_at") if isinstance(metadata, dict) else None
        age = time.time() - creating_at if isinstance(creating_at, (int, float)) else None
        if age is None or age > SANDBOX_CREATION_TIMEOUT:
            logger.warning(
                "Resetting stale SANDBOX_CREATING for thread %s (age=%s)", thread_id, age
            )
            await client.threads.update(thread_id=thread_id, metadata=_RESET_METADATA)
            return None

        await asyncio.sleep(SANDBOX_POLL_INTERVAL)


def graph_loaded_for_execution(config: RunnableConfig) -> bool:
    """Check if the graph is loaded for actual execution vs introspection."""
    return (
        config["configurable"].get("__is_for_execution__", False)
        if "configurable" in config
        else False
    )


async def ensure_sandbox_for_thread(
    thread_id: str,
    *,
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    repo: dict[str, str] | None = None,
) -> SandboxBackendProtocol:
    """Get-or-create a healthy sandbox bound to ``thread_id``.

    Implements the four-state lifecycle described in AGENTS.md:

    1. Cached in memory → ping; recreate on ``SandboxClientError``.
    2. Metadata says ``__creating__`` and no cache → wait for the creating
       worker; only reset if the sentinel is proven stale (timestamp/timeout).
    3. No sandbox at all → create one and persist the id.
    4. Metadata has an id but no cache → reconnect; recreate on failure.

    For LangSmith sandboxes, also refreshes the GitHub App proxy auth. When
    ``repo`` has a ``ready`` repo-scoped snapshot, newly created sandboxes boot
    from it; otherwise the configured ``DEFAULT_SANDBOX_SNAPSHOT_ID`` is used.
    Persists the resulting ``sandbox_id`` to thread metadata, and on the
    first creation/reconnect for this thread initializes git identity.
    """
    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    sandbox_id = await get_sandbox_id_from_metadata(thread_id)

    if sandbox_id == SANDBOX_CREATING and not sandbox_backend:
        logger.info("Sandbox creation in progress for thread %s, waiting...", thread_id)
        sandbox_id = await _resolve_creating_sentinel(thread_id)

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
        await client.threads.update(thread_id=thread_id, metadata=_creating_metadata())
        try:
            sandbox_backend = await _create_sandbox_with_proxy(
                github_proxy_token,
                thread_id=thread_id,
                github_proxy_repositories=github_proxy_repositories,
                repo=repo,
            )
            logger.info("Sandbox created: %s", sandbox_backend.id)
        except Exception:
            logger.exception("Failed to create sandbox")
            try:
                await client.threads.update(thread_id=thread_id, metadata=_RESET_METADATA)
            except Exception:
                logger.exception("Failed to reset sandbox_id metadata")
            raise
    else:
        logger.info("Connecting to existing sandbox %s", sandbox_id)
        created_replacement_sandbox = False
        try:
            sandbox_backend = await asyncio.to_thread(create_sandbox, sandbox_id)
        except Exception:
            logger.warning("Failed to connect to existing sandbox %s, creating new one", sandbox_id)
            await client.threads.update(thread_id=thread_id, metadata=_creating_metadata())
            try:
                sandbox_backend = await _create_sandbox_with_proxy(
                    github_proxy_token,
                    thread_id=thread_id,
                    github_proxy_repositories=github_proxy_repositories,
                    repo=repo,
                )
                created_replacement_sandbox = True
            except Exception:
                logger.exception("Failed to create replacement sandbox")
                await client.threads.update(thread_id=thread_id, metadata=_RESET_METADATA)
                raise
        if not created_replacement_sandbox:
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

    # Re-apply git identity every run: cached/reconnected sandboxes may have
    # lost their `--global` config (or had it overwritten), and Vercel preview
    # deploys reject commits whose author email can't be resolved to a GitHub
    # account.
    await _configure_git_identity(sandbox_backend)

    return sandbox_backend


DEFAULT_LLM_MODEL_ID = DEFAULT_MODEL_ID
DEFAULT_LLM_MAX_TOKENS = 64_000
DEFAULT_RECURSION_LIMIT = 9_999
# High cap to support long-running tasks; a run that hits it still ends with a
# signal via notify_step_limit_reached rather than dying silently.
MODEL_CALL_RECURSION_LIMIT = 5_000

# Mutating external tools hidden from the model while plan mode is active so it
# can only research and propose a plan. File edit tools stay available so the
# agent can draft and revise a plan under `/workspace/plans/`; prompt guidance
# restricts them to that plan file outside cloned repositories. `execute` stays available;
# plan-mode shell discipline (no mutating commands) is instructed via the system
# prompt rather than enforced. `http_request` is excluded because it can
# POST/PUT/PATCH/DELETE to external services — read-only web research goes
# through `web_search` / `fetch_url`. `task` is excluded because the
# general-purpose subagent is built with its own filesystem/PR/Linear tools and
# does not inherit this exclusion, so delegating to it would bypass the read-only
# intent.
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


def _get_cached_sandbox_backend(thread_id: str) -> SandboxBackendProtocol:
    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if sandbox_backend is None:
        raise RuntimeError(f"No sandbox backend cached for thread {thread_id}")
    return sandbox_backend


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


async def _load_observability_tools(authorized: bool) -> list[Any]:
    """Datadog (MCP) + LangSmith read tools when the team has connected them.

    Credentials live server-side in team settings; the sandbox never holds them.
    Only loaded for authorized (admin / allow-listed) triggering users so an
    untrusted run cannot exfiltrate team observability data. Failures degrade to
    no tools so the agent still starts.
    """
    if not authorized:
        return []
    try:
        datadog_tools, langsmith_tools = await asyncio.gather(
            load_datadog_tools(),
            load_langsmith_tools(),
        )
    except Exception:
        logger.warning("Failed to load observability tools", exc_info=True)
        return []
    return [*datadog_tools, *langsmith_tools]


async def _load_corridor_mcp_tools() -> list[Any]:
    """Corridor MCP tools when the deployment environment has configured them."""
    try:
        return await load_corridor_tools()
    except Exception:
        logger.warning("Failed to load Corridor MCP tools", exc_info=True)
        return []


async def get_agent(config: RunnableConfig) -> Pregel:
    """Get or create an agent with a sandbox for the given thread."""
    thread_id = config["configurable"].get("thread_id", None)

    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    if thread_id is None or not graph_loaded_for_execution(config):
        logger.info("No thread_id or not for execution, returning agent without sandbox")
        return create_deep_agent(
            system_prompt="",
            tools=[],
        ).with_config(config)

    github_token, _expires_at = await resolve_github_token(config, thread_id)
    profile_login = resolve_github_login(config)
    configurable = (config or {}).get("configurable") or {}
    prompt_default_repo = await _resolve_prompt_default_repo(configurable)
    triggering_user_identity_task = asyncio.create_task(
        asyncio.to_thread(resolve_triggering_user_identity, config, github_token)
    )
    sandbox_task = asyncio.create_task(
        ensure_sandbox_for_thread(thread_id, repo=prompt_default_repo)
    )
    team_defaults_task = asyncio.create_task(get_team_default_model_pair("agent"))
    profile_task = asyncio.create_task(load_profile(profile_login)) if profile_login else None
    triggering_user_identity, sandbox_backend, team_defaults = await asyncio.gather(
        triggering_user_identity_task,
        sandbox_task,
        team_defaults_task,
    )
    profile = await profile_task if profile_task is not None else None
    del github_token

    linear_issue = config["configurable"].get("linear_issue", {})
    linear_project_id = linear_issue.get("linear_project_id", "")
    linear_issue_number = linear_issue.get("linear_issue_number", "")

    work_dir = await aresolve_sandbox_work_dir(sandbox_backend)

    def backend_factory(_runtime: object, _thread_id: str = thread_id) -> SandboxBackendProtocol:
        return _get_cached_sandbox_backend(_thread_id)

    (model_id, profile_effort), (subagent_model_id, subagent_effort) = team_defaults
    logger.info("Using team default agent model: model=%s effort=%s", model_id, profile_effort)
    logger.info(
        "Using team default agent subagent model: model=%s effort=%s",
        subagent_model_id,
        subagent_effort,
    )

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
            ModelFallbackMiddleware(make_model(fallback_model_id, **fallback_kwargs))
        )
        logger.info("Configured model fallback %s -> %s", model_id, fallback_model_id)

    # Plan mode is entered only when the model decides to (the `enter_plan_mode`
    # tool sets it in run state). The configurable value just carries that
    # decision across a thread's messages and the approve/reject follow-ups; a
    # fresh run with nothing set starts out of plan mode.
    plan_mode = configurable.get("plan_mode") is True
    if plan_mode:
        logger.info("Plan mode enabled for thread %s", thread_id)
    # Installed unconditionally and state-aware: it also restricts tools after a
    # mid-run `enter_plan_mode` call, not just when plan mode is set up front.
    plan_mode_middleware: list[Any] = [
        PlanModeMiddleware(excluded=PLAN_MODE_EXCLUDED_TOOLS, initial=plan_mode)
    ]

    source = (
        configurable.get("source") if isinstance(configurable.get("source"), str) else "dashboard"
    )
    user_email = configurable.get("user_email")
    user_email = user_email if isinstance(user_email, str) else ""
    try:
        await client.threads.update(
            thread_id=thread_id,
            metadata={
                "agent_kind": "agent",
                "model": model_id,
                "effort": profile_effort,
                "source": source,
                "plan_mode": plan_mode,
            },
        )
        await record_agent_thread_usage(
            thread_id=thread_id,
            github_login=profile_login,
            user_email=user_email,
            model_id=model_id,
            effort=profile_effort,
            source=source,
        )
    except Exception:
        logger.debug("Failed to record agent usage for thread %s", thread_id, exc_info=True)

    repo_custom_instructions = await _resolve_repo_custom_instructions(prompt_default_repo)

    observability_tools = await _load_observability_tools(
        await _observability_authorized(config, profile_login)
    )
    corridor_tools = await _load_corridor_mcp_tools()

    currents_tools: list[Any] = []
    notion_tools: list[Any] = []
    if profile_login:
        try:
            currents_tools = await load_currents_tools(profile_login)
        except Exception:
            logger.warning("Failed to load Currents tools", exc_info=True)
            currents_tools = []
        try:
            notion_tools = await load_notion_tools(profile_login)
        except Exception:
            logger.warning("Failed to load Notion tools", exc_info=True)
            notion_tools = []

    logger.info("Returning agent with sandbox for thread %s", thread_id)
    main_model = make_model(model_id, **model_kwargs)
    subagent_model = make_model(subagent_model_id, **subagent_model_kwargs)
    return create_deep_agent(
        model=main_model,
        system_prompt=construct_system_prompt(
            working_dir=work_dir,
            linear_project_id=linear_project_id,
            linear_issue_number=linear_issue_number,
            triggering_user_identity=triggering_user_identity,
            create_prs=always_create_prs,
            default_repo=prompt_default_repo,
            plan_mode=plan_mode,
            plan_url=dashboard_plan_url(thread_id),
            repo_custom_instructions=repo_custom_instructions,
            thread_url=dashboard_thread_url(thread_id),
            corridor_enabled=bool(corridor_tools),
        ),
        tools=[
            http_request,
            fetch_url,
            web_search,
            enter_plan_mode,
            save_plan,
            linear_comment,
            linear_create_issue,
            linear_delete_issue,
            linear_get_issue,
            linear_get_issue_comments,
            linear_list_teams,
            linear_update_issue,
            open_pull_request,
            request_pr_review,
            schedule_thread_wakeup,
            slack_read_thread_messages,
            slack_start_new_thread,
            slack_thread_reply,
            *corridor_tools,
            *observability_tools,
            *currents_tools,
            *notion_tools,
        ],
        subagents=[_general_purpose_subagent(subagent_model)],
        backend=backend_factory,
        middleware=[
            SanitizeToolInputsMiddleware(),
            ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"),
            ToolErrorMiddleware(),
            ToolArtifactMiddleware(),
            WorkflowPushGuardMiddleware(),
            refresh_github_proxy_before_model,
            check_message_queue_before_model,
            SlackAssistantStatusMiddleware(),
            ensure_no_empty_msg,
            notify_step_limit_reached,
            SandboxCircuitBreakerMiddleware(),
            *fallback_middleware,
            *plan_mode_middleware,
            SanitizeThinkingBlocksMiddleware(),
        ],
    ).with_config(config)


traced_agent = traced_graph_factory(get_agent, AGENT_TRACING_PROJECT)
