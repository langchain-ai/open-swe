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
import posixpath
import warnings
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
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
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import BackendProtocol, SandboxBackendProtocol
from deepagents.backends.state import StateBackend
from deepagents.backends.store import StoreBackend
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolRetryMiddleware
from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from .dashboard.admin import is_admin, is_observability_authorized
from .dashboard.agent_overrides import (
    load_profile,
    normalize_profile_overrides,
    normalize_profile_subagent_overrides,
    profile_draft_prs,
    resolve_github_login,
)
from .dashboard.agent_usage import record_agent_run_usage
from .dashboard.environments import (
    SandboxResources,
    environment_prompt,
    environment_sandbox_create_params,
    environment_sandbox_resources,
    environment_snapshot_id,
    resolve_environment,
)
from .dashboard.options import (
    SUPPORTED_MODEL_IDS,
    canonical_model_pair,
    gate_fable_model,
    model_supports_effort,
)
from .dashboard.repo_snapshots import resolve_repo_snapshot_id
from .dashboard.run_diffs import THREAD_DIFF_KEY, save_run_diff
from .dashboard.sandbox_settings import get_admin_base_snapshot_id
from .dashboard.skills import ORGANIZATION_SKILLS_NAMESPACE, SKILLS_NAMESPACE
from .dashboard.team_settings import (
    get_effective_gateway_enabled,
    get_team_default_model_pair,
    get_team_default_repo,
    get_team_default_thread_title_model,
    get_team_fable_enabled,
)
from .dashboard.user_mappings import email_for_login
from .desktop import create_desktop_backend, desktop_artifact_routes, is_desktop_run
from .input_messages import append_message_data
from .integrations.corridor_mcp import load_corridor_tools
from .integrations.currents_tools import load_currents_tools
from .integrations.datadog_mcp import load_datadog_tools
from .integrations.langsmith import (
    _configure_github_proxy,
    _get_sandbox_proxy_config,
    create_langsmith_sandbox_from_params,
)
from .integrations.langsmith_tools import load_langsmith_tools
from .integrations.notion_mcp import load_notion_tools
from .integrations.stagehand_browser import load_browser_tools
from .middleware import (
    BasePrepareRunMiddleware,
    DynamicContextMiddleware,
    DynamicToolMiddleware,
    ExcludeToolsMiddleware,
    ModelCallTimeoutMiddleware,
    ModelFallbackMiddleware,
    PlanModeMiddleware,
    PullRequestCreationGuardMiddleware,
    SanitizeFireworksMessagesMiddleware,
    SanitizeOpenAIResponsesMiddleware,
    SanitizeThinkingBlocksMiddleware,
    SanitizeToolInputsMiddleware,
    SubdirAgentsReadMiddleware,
    TimeoutWrapupMiddleware,
    ToolErrorMiddleware,
    WorkflowPushGuardMiddleware,
    check_message_queue_before_model,
    notify_step_limit_reached,
    refresh_github_proxy_before_model,
    task_on_failure,
    task_retry_on,
)
from .middleware.prepare_run import PrepareRunState
from .middleware.sandbox_circuit_breaker import post_sandbox_unreachable_notification
from .prompt import construct_sender_context, construct_system_prompt, render_open_swe_shared_base
from .runtime.constants import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_RECURSION_LIMIT,
    MODEL_CALL_RECURSION_LIMIT,
)
from .runtime.constants import (
    DEFAULT_LLM_MODEL_ID as DEFAULT_LLM_MODEL_ID,
)
from .runtime.execution import graph_loaded_for_execution
from .thread_title import TITLE_GENERATION_MAX_TOKENS, schedule_thread_title_generation
from .tools import (
    approve_plan,
    background_execute,
    background_task,
    capture_environment_snapshot,
    create_sandbox_file_download_url,
    delete_environment,
    delete_organization_skill,
    delete_user_skill,
    enter_plan_mode,
    fetch_url,
    get_thread,
    http_request,
    linear_comment,
    linear_create_issue,
    linear_delete_issue,
    linear_get_issue,
    linear_get_issue_comments,
    linear_list_teams,
    linear_search_issues,
    linear_update_issue,
    list_environments,
    list_threads,
    manage_baby_sit,
    manage_thread,
    notify_automation_channel,
    open_pull_request,
    output_iframe,
    read_user_settings,
    recreate_sandbox,
    report_platform_issue,
    request_pr_review,
    sandbox_reset,
    save_environment,
    save_organization_skill,
    save_plan,
    save_user_instructions,
    save_user_skill,
    schedule_thread_wakeup,
    slack_add_reaction,
    slack_move_thread,
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
from .utils.dashboard_links import dashboard_base_url, dashboard_plan_url, dashboard_thread_url
from .utils.deferred_model import make_deferred_error_model
from .utils.github_app import get_github_app_installation_token_with_expiry
from .utils.github_org_membership import is_user_active_org_member
from .utils.github_proxy import get_recorded_proxy_base_config, record_proxy_token_expiry
from .utils.json_types import as_json_object
from .utils.model import (
    DEFAULT_LLM_REASONING,
    ModelKwargs,
    fallback_model_id_for,
    make_model,
    provider_model_kwargs,
)
from .utils.read_only_backend import ReadOnlyBackend
from .utils.sandbox import SandboxGoneError, create_sandbox
from .utils.sandbox_paths import aresolve_sandbox_work_dir
from .utils.sandbox_state import (
    SANDBOX_BACKENDS,
    SandboxBackendProxy,
    SandboxUnreachableError,
    get_or_create_sandbox_backend_proxy,
    get_sandbox_id_from_metadata,
    get_sandbox_metadata,
    set_sandbox_backend,
    unwrap_sandbox_backend,
)
from .utils.startup_trace import aphase
from .utils.thread_settings import (
    ThreadSettings,
    load_thread_settings,
    normalize_thread_settings,
    store_thread_settings,
)
from .utils.tracing import AGENT_TRACING_PROJECT, traced_graph_factory
from .utils.turn_checkpoint import merge_checkpoint, read_turn_diff, record_turn_checkpoint

client = get_client()

_SANDBOX_PROXY_CONFIG_METADATA_KEY = "sandbox_base_proxy_config"
DEFAULT_TOOL_LOADER_TIMEOUT_SECONDS = 5.0
USER_SKILLS_ROUTE = "/skills/"
ORGANIZATION_SKILLS_ROUTE = "/organization-skills/"
BUNDLED_SKILLS_ROUTE = "/bundled-skills/"
BUNDLED_SKILLS_DIR = Path(__file__).resolve().parent / "bundled_skills"
DEEP_AGENT_TOOL_NAMES = {
    "delete",
    "edit_file",
    "execute",
    "glob",
    "grep",
    "ls",
    "read_file",
    "task",
    "write_file",
}
DEEP_AGENT_EXCLUDED_TOOLS = frozenset({"grep"})
STOP_SUMMARY_EXCLUDED_TOOLS = DEEP_AGENT_EXCLUDED_TOOLS | frozenset(
    {"delete", "edit_file", "execute", "task", "write_file"}
)


def _registered_tool_name(value: Any) -> str:
    name = getattr(value, "name", None) or getattr(value, "__name__", None)
    if not isinstance(name, str) or not name:
        raise TypeError(f"tool has no registered name: {value!r}")
    return name


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


async def _resolve_user_custom_instructions(login: str | None) -> str | None:
    """Load user-level custom agent instructions for the triggering user."""
    if not login:
        return None
    try:
        from .dashboard.user_instructions import get_user_custom_instructions

        return await get_user_custom_instructions(login)
    except Exception:
        logger.debug("Failed to load user custom agent instructions", exc_info=True)
        return None


async def _resolve_proxy_token(
    github_proxy_token: str | None,
) -> tuple[str | None, str | None, None]:
    """Resolve the proxy token and its expiry."""
    if github_proxy_token:
        return github_proxy_token, None, None
    token, expires_at = await get_github_app_installation_token_with_expiry()
    return token, expires_at, None


async def _resolve_sandbox_create_config(
    repo: dict[str, str] | None,
    environment_slug: str | None = None,
) -> tuple[str | None, SandboxResources, dict[str, Any]]:
    """Resolve the snapshot, VM sizing, and create parameters for a new sandbox."""
    environment = await resolve_environment(environment_slug)
    environment_snapshot = environment_snapshot_id(environment)
    resources = environment_sandbox_resources(environment)
    create_params = environment_sandbox_create_params(environment)
    if environment_snapshot:
        return environment_snapshot, resources, create_params
    if repo:
        try:
            repo_snapshot_id = await resolve_repo_snapshot_id(repo.get("owner"), repo.get("name"))
        except Exception:  # noqa: BLE001
            logger.debug("Failed to resolve repo-scoped snapshot", exc_info=True)
            repo_snapshot_id = None
        if repo_snapshot_id:
            return repo_snapshot_id, resources, create_params
    return await get_admin_base_snapshot_id(), resources, create_params


async def _resolve_snapshot_id(
    repo: dict[str, str] | None,
    environment_slug: str | None = None,
) -> str | None:
    """Resolve the snapshot a new sandbox boots from."""
    snapshot_id, _, _ = await _resolve_sandbox_create_config(repo, environment_slug)
    return snapshot_id


async def _create_sandbox_with_proxy(
    github_proxy_token: str | None = None,
    *,
    thread_id: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    repo: dict[str, str] | None = None,
    environment_slug: str | None = None,
) -> SandboxBackendProtocol:
    """Create a new sandbox with GitHub proxy auth configured."""
    async with aphase(thread_id, "sandbox.resolve_snapshot"):
        snapshot_id, resources, create_params = await _resolve_sandbox_create_config(
            repo, environment_slug
        )
    async with aphase(thread_id, "sandbox.boot", snapshot_id=snapshot_id):
        if create_params:
            sandbox_backend = await create_sandbox(
                snapshot_id=snapshot_id,
                create_params=create_params,
                **resources,
            )
        else:
            sandbox_backend = await create_sandbox(snapshot_id=snapshot_id, **resources)

    sandbox_type = os.getenv("SANDBOX_TYPE", "langsmith")
    if sandbox_type == "langsmith":
        async with aphase(thread_id, "sandbox.proxy_token"):
            token, expires_at, permissions = await _resolve_proxy_token(github_proxy_token)
        if not token:
            msg = "Cannot configure proxy: GitHub App installation token is unavailable"
            logger.error(msg)
            raise ValueError(msg)
        proxy_config = _get_sandbox_proxy_config(create_params)
        async with aphase(thread_id, "sandbox.proxy_configure"):
            if proxy_config is not None:
                await _configure_github_proxy(
                    sandbox_backend.id,
                    token,
                    base_proxy_config=proxy_config,
                )
            else:
                await _configure_github_proxy(sandbox_backend.id, token)
        record_proxy_token_expiry(
            thread_id,
            expires_at,
            repositories=github_proxy_repositories,
            permissions=permissions,
            base_proxy_config=proxy_config,
        )

    return sandbox_backend


async def _refresh_github_proxy(
    sandbox_backend: SandboxBackendProtocol,
    github_proxy_token: str | None = None,
    *,
    thread_id: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    base_proxy_config: dict[str, Any] | None = None,
) -> None:
    """Refresh GitHub proxy credentials for reused LangSmith sandboxes."""
    if os.getenv("SANDBOX_TYPE", "langsmith") != "langsmith":
        return

    async with aphase(thread_id, "sandbox.proxy_token"):
        token, expires_at, permissions = await _resolve_proxy_token(github_proxy_token)
    if not token:
        logger.warning(
            "Skipping GitHub proxy refresh for sandbox %s: installation token unavailable",
            sandbox_backend.id,
        )
        return

    current_backend = unwrap_sandbox_backend(sandbox_backend)
    async with aphase(thread_id, "sandbox.proxy_refresh"):
        if base_proxy_config is not None:
            await _configure_github_proxy(
                current_backend.id,
                token,
                base_proxy_config=base_proxy_config,
            )
        else:
            await _configure_github_proxy(current_backend.id, token)
    record_proxy_token_expiry(
        thread_id,
        expires_at,
        repositories=github_proxy_repositories,
        permissions=permissions,
        base_proxy_config=base_proxy_config,
    )


async def _refresh_github_proxy_or_fail(
    sandbox_backend: SandboxBackendProtocol,
    thread_id: str,
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    base_proxy_config: dict[str, Any] | None = None,
) -> SandboxBackendProtocol:
    """Refresh proxy credentials; a sandbox we can't reconfigure is unreachable."""
    try:
        await _refresh_github_proxy(
            sandbox_backend,
            github_proxy_token,
            thread_id=thread_id,
            github_proxy_repositories=github_proxy_repositories,
            base_proxy_config=base_proxy_config,
        )
    except Exception as exc:
        logger.warning(
            "Failed to refresh GitHub proxy for sandbox %s on thread %s",
            sandbox_backend.id,
            thread_id,
            exc_info=True,
        )
        raise SandboxUnreachableError(thread_id, sandbox_backend.id, str(exc)) from exc
    return sandbox_backend


async def _configure_git_identity(sandbox_backend: SandboxBackendProtocol) -> None:
    await sandbox_backend.aexecute(
        f"git config --global user.name '{OPEN_SWE_BOT_NAME}' && "
        f"git config --global user.email '{OPEN_SWE_BOT_EMAIL}'",
    )


async def _connect_existing_sandbox(
    thread_id: str,
    *,
    cached: SandboxBackendProtocol | None,
    sandbox_id: str | None,
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    base_proxy_config: dict[str, Any] | None = None,
) -> SandboxBackendProtocol:
    """Reuse the sandbox already bound to ``thread_id``, or fail unreachable.

    A ``SandboxGoneError`` propagates untouched so the caller recreates. Nothing
    pings the box first: refreshing the proxy below has to reach it anyway, and
    raises the same unreachable error when it cannot.
    """
    if cached is not None:
        logger.info("Using cached sandbox backend for thread %s", thread_id)
        sandbox_backend = cached
    else:
        logger.info("Connecting to existing sandbox %s", sandbox_id)
        try:
            async with aphase(thread_id, "sandbox.reconnect", sandbox_id=sandbox_id):
                sandbox_backend = await create_sandbox(str(sandbox_id))
        except SandboxGoneError:
            raise
        except Exception as exc:
            logger.warning("Failed to connect to existing sandbox %s", sandbox_id)
            raise SandboxUnreachableError(thread_id, sandbox_id, str(exc)) from exc
    return await _refresh_github_proxy_or_fail(
        sandbox_backend,
        thread_id,
        github_proxy_token,
        github_proxy_repositories,
        base_proxy_config,
    )


async def ensure_sandbox_for_thread(
    thread_id: str,
    *,
    github_proxy_token: str | None = None,
    github_proxy_repositories: Sequence[str] | None = None,
    repo: dict[str, str] | None = None,
    environment_slug: str | None = None,
    allow_replacement: bool = False,
) -> SandboxBackendProtocol:
    """Get-or-create a healthy sandbox bound to ``thread_id``.

    Three cases (dispatch uses ``multitask_strategy="interrupt"``, so a thread
    never provisions two sandboxes concurrently — no cross-process sentinel is
    needed):

    1. Cached in memory -> ping, then refresh proxy.
    2. Metadata has an id -> reconnect, then refresh proxy.
    3. No sandbox at all -> create one and persist the id.

    A sandbox that exists but can't be reached raises ``SandboxUnreachableError``
    instead of being replaced, because a replacement is empty and swapping one in
    silently destroys whatever the agent had not yet committed. A *deleted* one
    (``SandboxGoneError``) is always replaced: it holds nothing, and the stale id
    in thread metadata is what every later run keeps reconnecting to, so refusing
    would brick the thread permanently.

    ``allow_replacement`` extends replacement to merely unreachable sandboxes,
    for callers whose sandbox holds nothing but a re-derivable checkout — the
    read-only reviewer, which re-preps the repo every run.

    For LangSmith sandboxes, also refreshes the GitHub App proxy auth. When
    ``repo`` has a ``ready`` repo-scoped snapshot, newly created sandboxes boot
    from it; otherwise the base snapshot (admin setting, else
    ``DEFAULT_SANDBOX_SNAPSHOT_ID``) is used.
    Re-applies git identity every run because reused/reconnected sandboxes can
    lose their ``--global`` config, and Vercel preview deploys reject commits
    whose author email can't be resolved to a GitHub account.
    """
    cached_proxy = SANDBOX_BACKENDS.get(thread_id)
    sandbox_backend = (
        unwrap_sandbox_backend(cached_proxy)
        if cached_proxy is not None and cached_proxy.has_backend
        else None
    )
    async with aphase(thread_id, "sandbox.thread_metadata"):
        sandbox_id = await get_sandbox_id_from_metadata(thread_id)
        sandbox_metadata = await get_sandbox_metadata(thread_id) if sandbox_id is not None else {}
    metadata_proxy_config = sandbox_metadata.get(_SANDBOX_PROXY_CONFIG_METADATA_KEY)
    base_proxy_config = (
        metadata_proxy_config
        if isinstance(metadata_proxy_config, dict)
        else get_recorded_proxy_base_config(thread_id)
    )
    created_proxy_config: dict[str, Any] | None = None

    if sandbox_backend is None and sandbox_id is None:
        logger.info("Creating new sandbox for thread %s", thread_id)
        sandbox_backend = await _create_sandbox_with_proxy(
            github_proxy_token,
            thread_id=thread_id,
            github_proxy_repositories=github_proxy_repositories,
            repo=repo,
            environment_slug=environment_slug,
        )
        created_proxy_config = get_recorded_proxy_base_config(thread_id)
        logger.info("Sandbox created: %s", sandbox_backend.id)
    else:
        try:
            sandbox_backend = await _connect_existing_sandbox(
                thread_id,
                cached=sandbox_backend,
                sandbox_id=sandbox_id,
                github_proxy_token=github_proxy_token,
                github_proxy_repositories=github_proxy_repositories,
                base_proxy_config=base_proxy_config,
            )
        except (SandboxGoneError, SandboxUnreachableError) as exc:
            gone = isinstance(exc, SandboxGoneError)
            if not (gone or allow_replacement):
                raise
            logger.warning(
                "Replacing %s sandbox %s for thread %s",
                "deleted" if gone else "unreachable",
                sandbox_id,
                thread_id,
            )
            try:
                sandbox_backend = await _create_sandbox_with_proxy(
                    github_proxy_token,
                    thread_id=thread_id,
                    github_proxy_repositories=github_proxy_repositories,
                    repo=repo,
                    environment_slug=environment_slug,
                )
                created_proxy_config = get_recorded_proxy_base_config(thread_id)
            except Exception as create_exc:
                # Keep the failure typed so callers still recognize "this run has no
                # sandbox" and can notify the user.
                logger.warning(
                    "Failed to replace sandbox %s for thread %s",
                    sandbox_id,
                    thread_id,
                    exc_info=True,
                )
                raise SandboxUnreachableError(
                    thread_id, sandbox_id, str(create_exc)
                ) from create_exc
            logger.info("Replacement sandbox created: %s", sandbox_backend.id)

    async with aphase(thread_id, "sandbox.git_identity"):
        await _configure_git_identity(sandbox_backend)

    # Bind the thread only once the sandbox is created and initialized: a run
    # that dies earlier leaves no id to reconnect to, so the next run creates
    # rather than adopting a half-built box.
    if sandbox_id != sandbox_backend.id:
        sandbox_metadata: dict[str, Any] = {"sandbox_id": sandbox_backend.id}
        if created_proxy_config is not None:
            sandbox_metadata[_SANDBOX_PROXY_CONFIG_METADATA_KEY] = created_proxy_config
        async with aphase(thread_id, "sandbox.bind_thread"):
            await client.threads.update(thread_id=thread_id, metadata=sandbox_metadata)

    # Publishing last is what makes a failure above visible. Callers reach the
    # proxy's cached backend without awaiting the startup task that produced it,
    # so a backend published before this point would be used by the rest of the
    # run while the initialization that failed is only logged.
    return set_sandbox_backend(thread_id, sandbox_backend)


async def reset_sandbox_for_thread(
    thread_id: str,
    create_params: dict[str, Any],
) -> tuple[str, str]:
    """Bind a thread to a fresh sandbox created from raw provider options."""
    if os.getenv("SANDBOX_TYPE", "langsmith") != "langsmith":
        raise ValueError("sandbox_reset is only supported by the LangSmith sandbox provider")

    cached = SANDBOX_BACKENDS.get(thread_id)
    metadata_sandbox_id = await get_sandbox_id_from_metadata(thread_id)
    old_sandbox_id = cached.id if cached is not None and cached.has_backend else metadata_sandbox_id
    if not old_sandbox_id:
        raise ValueError(f"Thread {thread_id} has no sandbox to reset")

    new_sandbox = await create_langsmith_sandbox_from_params(create_params)
    if new_sandbox.id == old_sandbox_id:
        raise RuntimeError("Sandbox provider did not create a distinct sandbox")

    proxy_config = _get_sandbox_proxy_config(create_params)
    token, expires_at, permissions = await _resolve_proxy_token(None)
    if not token:
        raise ValueError("Cannot configure proxy: GitHub App installation token is unavailable")
    if proxy_config is not None:
        await _configure_github_proxy(new_sandbox.id, token, base_proxy_config=proxy_config)
    else:
        await _configure_github_proxy(new_sandbox.id, token)
    record_proxy_token_expiry(
        thread_id,
        expires_at,
        permissions=permissions,
        base_proxy_config=proxy_config,
    )

    await _configure_git_identity(new_sandbox)
    sandbox_metadata: dict[str, Any] = {"sandbox_id": new_sandbox.id}
    if proxy_config is not None:
        sandbox_metadata[_SANDBOX_PROXY_CONFIG_METADATA_KEY] = proxy_config
    await client.threads.update(thread_id=thread_id, metadata=sandbox_metadata)
    set_sandbox_backend(thread_id, new_sandbox)
    logger.info(
        "Reset thread %s from sandbox %s to sandbox %s",
        thread_id,
        old_sandbox_id,
        new_sandbox.id,
    )
    return old_sandbox_id, new_sandbox.id


async def recreate_sandbox_for_thread(
    thread_id: str,
    *,
    repo: dict[str, str] | None = None,
    environment_slug: str | None = None,
) -> tuple[str, str]:
    """Bind a thread to a fresh sandbox while preserving its previous sandbox."""
    cached = SANDBOX_BACKENDS.get(thread_id)
    metadata_sandbox_id = await get_sandbox_id_from_metadata(thread_id)
    old_sandbox_id = cached.id if cached is not None and cached.has_backend else metadata_sandbox_id
    if not old_sandbox_id:
        raise ValueError(f"Thread {thread_id} has no sandbox to recreate")

    new_sandbox = await _create_sandbox_with_proxy(
        thread_id=thread_id,
        repo=repo,
        environment_slug=environment_slug,
    )
    if new_sandbox.id == old_sandbox_id:
        raise RuntimeError("Sandbox provider did not create a distinct sandbox")

    await _configure_git_identity(new_sandbox)
    sandbox_metadata: dict[str, Any] = {"sandbox_id": new_sandbox.id}
    base_proxy_config = get_recorded_proxy_base_config(thread_id)
    if base_proxy_config is not None:
        sandbox_metadata[_SANDBOX_PROXY_CONFIG_METADATA_KEY] = base_proxy_config
    await client.threads.update(
        thread_id=thread_id,
        metadata=sandbox_metadata,
    )
    set_sandbox_backend(thread_id, new_sandbox)
    logger.info(
        "Rebound thread %s from sandbox %s to sandbox %s",
        thread_id,
        old_sandbox_id,
        new_sandbox.id,
    )
    return old_sandbox_id, new_sandbox.id


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
        "background_execute",
        "background_task",
        "http_request",
        "manage_baby_sit",
        "manage_thread",
        "open_pull_request",
        "recreate_sandbox",
        "sandbox_reset",
        "request_pr_review",
        "save_user_skill",
        "delete_user_skill",
        "slack_move_thread",
        "slack_start_new_thread",
        "linear_create_issue",
        "linear_update_issue",
        "linear_delete_issue",
        "save_environment",
        "capture_environment_snapshot",
        "delete_environment",
    }
)


def _subagent_model_middleware() -> list[AgentMiddleware[Any, Any, Any]]:
    """Provider guards for subagent model calls.

    Subagents compile into their own graphs, so parent middleware never wraps them.
    """
    return cast(
        list[AgentMiddleware[Any, Any, Any]],
        [SanitizeOpenAIResponsesMiddleware(), ModelCallTimeoutMiddleware()],
    )


def _is_subagent_excluded_tool(tool: Any) -> bool:
    """Return whether a tool depends on parent-only source context."""
    name = getattr(tool, "name", None) or getattr(tool, "__name__", "")
    return name.startswith("slack_") or name in {
        "get_thread",
        "list_threads",
        "manage_thread",
        "notify_automation_channel",
        "read_user_settings",
    }


def _general_purpose_subagent(
    model: BaseChatModel,
    tools: Sequence[Any],
    skills: list[str] | None = None,
    dynamic_tools: DynamicToolMiddleware | None = None,
    *,
    sandbox_file_downloads: bool = False,
) -> SubAgent:
    subagent: SubAgent = {
        "name": GENERAL_PURPOSE_SUBAGENT["name"],
        "description": (
            GENERAL_PURPOSE_SUBAGENT["description"]
            + " It cannot access Slack tools; relay all Slack communication from the main agent."
        ),
        # Deep Agents' default GP prompt covers only task mechanics; the shared
        # base carries the Open SWE identity and conventions (gh proxy usage,
        # tool-call cadence) that delegated work also needs.
        "system_prompt": render_open_swe_shared_base(sandbox_file_downloads=sandbox_file_downloads)
        + "\n\n"
        + GENERAL_PURPOSE_SUBAGENT["system_prompt"],
        "model": model,
        "tools": [tool for tool in tools if not _is_subagent_excluded_tool(tool)],
        "middleware": [
            *([dynamic_tools] if dynamic_tools else []),
            ExcludeToolsMiddleware(excluded=DEEP_AGENT_EXCLUDED_TOOLS),
            *_subagent_model_middleware(),
        ],
    }
    if skills:
        subagent["skills"] = skills
    return subagent


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
        "middleware": _subagent_model_middleware(),
    }


def _get_cached_sandbox_backend(
    thread_id: str,
    *,
    reconnect: Callable[[], Awaitable[SandboxBackendProtocol]] | None = None,
) -> SandboxBackendProxy:
    return get_or_create_sandbox_backend_proxy(thread_id, reconnect=reconnect)


get_cached_sandbox_backend = _get_cached_sandbox_backend
configure_git_identity = _configure_git_identity


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


# Added to an admin thread's tools; see the admin-thread section of the prompt.
ADMIN_TOOLS = (
    sandbox_reset,
    list_environments,
    save_environment,
    capture_environment_snapshot,
    delete_environment,
    save_organization_skill,
    delete_organization_skill,
)


def _environment_slug(configurable: Mapping[str, Any] | None) -> str | None:
    """The environment this thread selected, if any."""
    slug = (configurable or {}).get("environment")
    return slug.strip() or None if isinstance(slug, str) else None


async def _workspace_admin(config: RunnableConfig, profile_login: str | None) -> bool:
    configurable = (config or {}).get("configurable") or {}
    config_login = configurable.get("github_login")
    login = profile_login or (config_login if isinstance(config_login, str) else None)
    email = configurable.get("user_email")
    if is_admin(email if isinstance(email, str) else None, login=login):
        return True
    return is_admin(await email_for_login(login), login=login)


async def _admin_thread(config: RunnableConfig, profile_login: str | None) -> bool:
    """Whether this run may manage environments and organization skills.

    The dashboard only stamps ``admin_thread`` for an admin session, but the flag
    is re-checked here against ``CONFIGURED_ADMINS`` so a thread cannot carry the
    capability to a non-admin who later messages it.
    """
    configurable = (config or {}).get("configurable") or {}
    return configurable.get("admin_thread") is True and await _workspace_admin(
        config, profile_login
    )


async def _allowed_org_member(config: RunnableConfig, profile_login: str | None) -> bool:
    configurable = (config or {}).get("configurable") or {}
    config_login = configurable.get("github_login")
    login = profile_login or (config_login if isinstance(config_login, str) else None)
    if not login:
        return False
    orgs = dict.fromkeys(
        org.strip().lower()
        for org in os.environ.get("ALLOWED_GITHUB_ORGS", "").split(",")
        if org.strip()
    )
    for org in orgs:
        if await is_user_active_org_member(login, org):
            return True
    return False


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


async def _load_observability_tools(authorized: bool, profile_login: str | None) -> list[Any]:
    """Load team observability tools for an authorized triggering user."""
    if not authorized:
        return []
    datadog_tools, langsmith_tools = await asyncio.gather(
        _cached_tool_loader("tools:datadog", 600, load_datadog_tools),
        load_langsmith_tools(profile_login),
    )
    return [*datadog_tools, *langsmith_tools]


async def _load_corridor_mcp_tools() -> list[Any]:
    """Corridor MCP tools when the deployment environment has configured them."""
    return await _cached_tool_loader("tools:corridor", 600, load_corridor_tools)


async def _cached_team_default_model_pair(kind: Literal["agent", "reviewer"]):
    return await ttl_cache.cached(
        f"team-default-model-pair:{kind}",
        60,
        lambda: get_team_default_model_pair(kind),
    )


async def _cached_thread_title_model() -> tuple[str, str]:
    return await ttl_cache.cached(
        "team:thread-title-model",
        60,
        get_team_default_thread_title_model,
    )


async def _cached_gateway_enabled() -> bool:
    return await ttl_cache.cached(
        "team:gateway-enabled",
        60,
        get_effective_gateway_enabled,
    )


async def _cached_fable_enabled() -> bool:
    return await ttl_cache.cached(
        "team:fable-enabled",
        60,
        get_team_fable_enabled,
    )


async def _cached_profile(profile_login: str | None):
    if not profile_login:
        return None
    return await ttl_cache.cached(
        f"profile:{profile_login}", 30, lambda: load_profile(profile_login)
    )


def _sandbox_file_downloads_enabled(configurable: dict[str, Any] | None = None) -> bool:
    """Return whether signed sandbox file downloads are available for this run."""
    resolved = configurable or {}
    return (
        os.getenv("SANDBOX_TYPE", "langsmith") == "langsmith"
        and resolved.get("stop_summary") is not True
        and not is_desktop_run(resolved)
    )


def _slack_tools_enabled(configurable: dict[str, Any]) -> bool:
    """Return whether the run has trusted Slack source context."""
    if configurable.get("source") not in {"slack", "schedule"}:
        return False
    slack_thread = configurable.get("slack_thread")
    if not isinstance(slack_thread, dict):
        return False
    return all(
        isinstance(slack_thread.get(key), str) and bool(slack_thread[key].strip())
        for key in ("channel_id", "thread_ts")
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
        repo_instructions: str | None,
        model_id: str,
        effort: str | None,
        title_model: BaseChatModel,
        source: str,
        user_email: str,
        linear_project_id: str,
        linear_issue_number: str,
        draft_prs: bool,
        plan_mode: bool,
        corridor_enabled: bool,
        admin_environments: bool,
    ) -> None:
        self._thread_id = thread_id
        self._config = config
        self._profile_login = profile_login
        self._repo_instructions = repo_instructions
        self._model_id = model_id
        self._effort = effort
        self._title_model = title_model
        self._source = source
        self._user_email = user_email
        self._linear_project_id = linear_project_id
        self._linear_issue_number = linear_issue_number
        self._draft_prs = draft_prs
        self._plan_mode = plan_mode
        self._corridor_enabled = corridor_enabled
        self._admin_environments = admin_environments

    def _prepare_config_fingerprint(self) -> Any:
        configurable = (self._config or {}).get("configurable") or {}
        return {
            "prepare_run_id": configurable.get("prepare_run_id"),
            "thread_id": self._thread_id,
            "source": self._source,
            "repo": configurable.get("repo"),
            "plan_mode": self._plan_mode,
            "draft_prs": self._draft_prs,
            "model": self._model_id,
            "effort": self._effort,
        }

    @staticmethod
    def _turn_key(state: Mapping[str, Any]) -> str | None:
        return next(
            (
                message.id
                for message in reversed(state.get("messages") or [])
                if isinstance(message, HumanMessage) and message.id
            ),
            None,
        )

    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> None:  # noqa: ARG002
        turn_key = self._turn_key(state)
        if not turn_key:
            return None
        try:
            thread = await client.threads.get(thread_id=self._thread_id)
            checkpoints = (thread.get("metadata") or {}).get("turn_checkpoints") or []
            checkpoint = next(
                entry
                for entry in reversed(checkpoints)
                if isinstance(entry, Mapping) and entry.get("key") == turn_key
            )
            first = next(entry for entry in checkpoints if isinstance(entry, Mapping))
            if checkpoint.get("repo_path") != first.get("repo_path"):
                return None
            backend = await get_or_create_sandbox_backend_proxy(self._thread_id).ready()
            plan_ref = checkpoint.get("plan_ref")
            head = plan_ref if isinstance(plan_ref, str) else None
            repo_path = (
                checkpoint.get("repo_path")
                if isinstance(checkpoint.get("repo_path"), str)
                else None
            )
            diff = await read_turn_diff(
                backend, None, str(checkpoint["ref"]), head, repo_path=repo_path
            )
            cumulative = (
                diff
                if first["ref"] == checkpoint["ref"]
                else await read_turn_diff(
                    backend, None, str(first["ref"]), head, repo_path=repo_path
                )
            )
            if diff.get("status") == "ready":
                await save_run_diff(self._thread_id, turn_key, diff)
            if cumulative.get("status") == "ready":
                await save_run_diff(self._thread_id, THREAD_DIFF_KEY, cumulative)
        except Exception:
            logger.debug("Could not persist run diff for %s", self._thread_id, exc_info=True)
        return None

    async def _record_turn_checkpoint(
        self,
        state: PrepareRunState,
        sandbox_backend: Any,
        work_dir: str,
        preferred_repo_path: str | None,
    ) -> list[dict[str, Any]] | None:
        """Snapshot the worktree so the dashboard can diff this turn from git.

        Keyed by the user message that opened the turn, which is the same id the
        client groups an assistant turn under.
        """
        turn_key = self._turn_key(state)
        if not turn_key:
            return None
        checkpoint = await record_turn_checkpoint(
            sandbox_backend,
            work_dir,
            turn_key,
            repo_path=preferred_repo_path,
        )
        if checkpoint is None:
            return None
        ref, repo_path = checkpoint
        try:
            thread = await client.threads.get(thread_id=self._thread_id)
            existing = (thread.get("metadata") or {}).get("turn_checkpoints")
        except Exception:
            logger.debug("Could not read turn checkpoints for %s", self._thread_id, exc_info=True)
            existing = None
        return merge_checkpoint(
            existing,
            turn_key,
            ref,
            datetime.now(UTC).isoformat(),
            repo_path=repo_path,
            plan_mode=self._plan_mode,
        )

    @staticmethod
    def _sender_context_message(state: PrepareRunState, sender_context: str) -> HumanMessage | None:
        message = next(
            (
                candidate
                for candidate in reversed(state.get("messages") or [])
                if isinstance(candidate, HumanMessage)
            ),
            None,
        )
        if message is None:
            return None
        content = message.content
        # Inside the envelope, not after it: a trailing sibling makes the whole
        # message unparseable and the transcript renders it as raw markup.
        spliced = (
            append_message_data(content, "sender_context", sender_context)
            if isinstance(content, (str, list))
            else None
        )
        if spliced is not None:
            return message.model_copy(update={"content": spliced})
        wrapped = f"<sender_context>\n{sender_context}\n</sender_context>"
        updated_content: Any
        if isinstance(content, str):
            updated_content = f"{content}\n\n{wrapped}"
        elif isinstance(content, list):
            updated_content = [*content, {"type": "text", "text": wrapped}]
        else:
            updated_content = [content, {"type": "text", "text": wrapped}]
        return message.model_copy(update={"content": updated_content})

    async def _prepare(self, state: PrepareRunState, runtime: Runtime) -> dict[str, Any]:  # noqa: ARG002
        schedule_thread_title_generation(
            thread_id=self._thread_id,
            messages=state.get("messages") or [],
            model=self._title_model,
            client=client,
        )
        configurable = (self._config or {}).get("configurable") or {}
        configurable["draft_prs"] = self._draft_prs
        if is_desktop_run(configurable):
            async with aphase(self._thread_id, "prepare.await_sandbox"):
                sandbox_backend = await get_or_create_sandbox_backend_proxy(self._thread_id).ready()
            async with aphase(self._thread_id, "prepare.work_dir"):
                work_dir = await aresolve_sandbox_work_dir(sandbox_backend)
            return {
                "work_dir": work_dir,
                "rendered_system_prompt": construct_system_prompt(
                    working_dir=work_dir,
                    source="desktop",
                ),
            }
        async with aphase(self._thread_id, "prepare.github_token"):
            github_token, _expires_at = await resolve_github_token(self._config, self._thread_id)
        async with aphase(self._thread_id, "prepare.default_repo"):
            prompt_default_repo = await _resolve_prompt_default_repo(configurable)
        triggering_user_identity_task = asyncio.create_task(
            asyncio.to_thread(
                resolve_triggering_user_identity, as_json_object(self._config), github_token
            )
        )
        sandbox_task = asyncio.create_task(
            get_or_create_sandbox_backend_proxy(self._thread_id).ready()
        )
        try:
            async with aphase(self._thread_id, "prepare.await_sandbox"):
                triggering_user_identity, sandbox_backend = await asyncio.gather(
                    triggering_user_identity_task,
                    sandbox_task,
                )
        except SandboxUnreachableError as exc:
            # The run is about to die with no sandbox; make sure the user hears
            # why rather than getting silence.
            await post_sandbox_unreachable_notification(
                self._config or {}, sandbox_id=exc.sandbox_id
            )
            raise
        del github_token
        async with aphase(self._thread_id, "prepare.work_dir"):
            work_dir = await aresolve_sandbox_work_dir(sandbox_backend)
        async with aphase(self._thread_id, "prepare.environment"):
            environment = await resolve_environment(_environment_slug(configurable))
        async with aphase(self._thread_id, "prepare.sender_context"):
            sender_instructions = await _resolve_user_custom_instructions(self._profile_login)
            sender_context = construct_sender_context(
                triggering_user_identity,
                user_custom_instructions=sender_instructions,
                draft_prs=self._draft_prs,
                thread_url=dashboard_thread_url(self._thread_id),
                workspace_admin=await _workspace_admin(self._config or {}, self._profile_login),
            )
        sender_message = self._sender_context_message(state, sender_context)
        preferred_repo_path = (
            posixpath.join(work_dir, prompt_default_repo["name"])
            if prompt_default_repo and prompt_default_repo.get("name")
            else None
        )
        async with aphase(self._thread_id, "prepare.turn_checkpoint"):
            turn_checkpoints = await self._record_turn_checkpoint(
                state, sandbox_backend, work_dir, preferred_repo_path
            )
        try:
            async with aphase(self._thread_id, "prepare.record_run"):
                await client.threads.update(
                    thread_id=self._thread_id,
                    metadata={
                        "agent_kind": "agent",
                        "model": self._model_id,
                        "effort": self._effort,
                        "source": self._source,
                        "plan_mode": self._plan_mode,
                        **({"turn_checkpoints": turn_checkpoints} if turn_checkpoints else {}),
                    },
                )
                prepare_run_id = configurable.get("prepare_run_id")
                if isinstance(prepare_run_id, str):
                    await record_agent_run_usage(
                        run_id=prepare_run_id,
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
            **({"messages": [sender_message]} if sender_message else {}),
            "rendered_system_prompt": construct_system_prompt(
                working_dir=work_dir,
                dashboard_base_url=dashboard_base_url(),
                linear_project_id=self._linear_project_id,
                linear_issue_number=self._linear_issue_number,
                default_repo=prompt_default_repo,
                plan_mode=self._plan_mode,
                plan_url=dashboard_plan_url(self._thread_id),
                repo_custom_instructions=self._repo_instructions,
                corridor_enabled=self._corridor_enabled,
                environment_name=(environment or {}).get("name"),
                environment_instructions=environment_prompt(environment),
                admin_environments=self._admin_environments,
                source=self._source,
                slack_context=_slack_tools_enabled(configurable),
                sandbox_file_downloads=_sandbox_file_downloads_enabled(configurable),
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

    async def reconnect_backend(
        _thread_id: str = thread_id,
        _configurable: dict[str, Any] = configurable,
    ) -> SandboxBackendProtocol:
        if is_desktop_run(_configurable):
            return create_desktop_backend(_configurable)
        async with aphase(_thread_id, "sandbox.default_repo"):
            prompt_default_repo = await _resolve_prompt_default_repo(_configurable)
        return await ensure_sandbox_for_thread(
            _thread_id,
            repo=prompt_default_repo,
            environment_slug=_environment_slug(_configurable),
        )

    backend = _get_cached_sandbox_backend(thread_id, reconnect=reconnect_backend)
    backend.start()

    # `profile_login` is whoever sent the message that started this run; it drives
    # authorization and credentialed integrations, which stay personal to them.
    # Everything else comes from the thread's own settings, resolved from the
    # owner's profile on the first run and frozen there afterwards.
    local_run = is_desktop_run(configurable)
    profile_login = resolve_github_login(as_json_object(config))
    async with aphase(thread_id, "factory.thread_settings"):
        thread_settings, settings_changed = normalize_thread_settings(
            {} if local_run else await load_thread_settings(client, thread_id)
        )
    settings_login = thread_settings.get("owner_login") or profile_login
    # Team/profile settings are accepted stale for a short TTL so graph factories
    # stay off the critical path during worker load and retry storms.
    if local_run:
        from .dashboard.options import default_model_pair

        team_defaults = (default_model_pair(), default_model_pair())
        title_defaults = team_defaults[0]
        use_gateway = False
        profile = None
        fable_enabled = False
    else:
        async with aphase(thread_id, "factory.settings_defaults"):
            (
                team_defaults,
                title_defaults,
                use_gateway,
                profile,
                fable_enabled,
            ) = await asyncio.gather(
                _cached_team_default_model_pair("agent"),
                _cached_thread_title_model(),
                _cached_gateway_enabled(),
                _cached_profile(None if thread_settings.get("model_id") else settings_login),
                _cached_fable_enabled(),
            )

    linear_issue = as_json_object(configurable.get("linear_issue"))
    linear_project_id = linear_issue.get("linear_project_id", "")
    linear_issue_number = linear_issue.get("linear_issue_number", "")

    (model_id, profile_effort), (subagent_model_id, subagent_effort) = team_defaults
    title_model_id, title_effort = title_defaults
    logger.info("Using team default agent model: model=%s effort=%s", model_id, profile_effort)

    if settings_login and profile:
        overridden_model, overridden_effort = normalize_profile_overrides(profile)
        if overridden_model:
            logger.info(
                "Applying dashboard profile override for %s: model=%s effort=%s",
                settings_login,
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
                settings_login,
                overridden_subagent_model,
                overridden_subagent_effort,
            )
            subagent_model_id = overridden_subagent_model
            subagent_effort = overridden_subagent_effort

    stored_model = thread_settings.get("model_id")
    if isinstance(stored_model, str):
        model_id = stored_model
        profile_effort = thread_settings.get("effort")
        subagent_model_id = thread_settings.get("subagent_model_id") or stored_model
        subagent_effort = thread_settings.get("subagent_effort")
        logger.info("Using stored thread settings: model=%s effort=%s", model_id, profile_effort)

    # An explicit per-run model choice is the one thing allowed to move a thread
    # off its stored settings; the new choice is then stored in turn.
    per_thread_model = configurable.get("agent_model_id")
    per_thread_effort = configurable.get("agent_effort")
    canonical_per_thread = canonical_model_pair(per_thread_model, per_thread_effort)
    if canonical_per_thread is not None:
        per_thread_model, per_thread_effort = canonical_per_thread
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

    async with aphase(thread_id, "factory.sender_profile"):
        sender_profile = (
            profile
            if profile_login == settings_login and profile is not None
            else await _cached_profile(profile_login)
        )
    sender_draft_prs = profile_draft_prs(sender_profile)
    configurable["draft_prs"] = sender_draft_prs
    if isinstance(thread_settings.get("model_id"), str):
        repo_instructions = thread_settings.get("repo_instructions")
    else:
        async with aphase(thread_id, "factory.repo_instructions"):
            repo_instructions = await _resolve_repo_custom_instructions(
                await _resolve_prompt_default_repo(configurable)
            )
    # Stored before the Fable gate so a deployment-wide toggle still applies on
    # every run rather than being frozen into the thread.
    resolved_settings: ThreadSettings = {
        "owner_login": settings_login,
        "model_id": model_id,
        "effort": profile_effort,
        "subagent_model_id": subagent_model_id,
        "subagent_effort": subagent_effort,
        "repo_instructions": repo_instructions,
    }
    if not local_run and (
        settings_changed or {**thread_settings, **resolved_settings} != thread_settings
    ):
        async with aphase(thread_id, "factory.store_settings"):
            await store_thread_settings(client, thread_id, {**thread_settings, **resolved_settings})

    model_id, profile_effort = gate_fable_model(
        model_id, profile_effort, fable_enabled=fable_enabled
    )
    subagent_model_id, subagent_effort = gate_fable_model(
        subagent_model_id, subagent_effort, fable_enabled=fable_enabled
    )
    title_model_id, title_effort = gate_fable_model(
        title_model_id, title_effort, fable_enabled=fable_enabled
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
    title_model_kwargs = provider_model_kwargs(
        title_model_id,
        title_effort,
        max_tokens=TITLE_GENERATION_MAX_TOKENS,
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

    async with aphase(thread_id, "factory.admin_thread"):
        admin_thread = await _admin_thread(config, profile_login)
    if admin_thread:
        logger.info("Admin thread %s: adding workspace management tools", thread_id)

    stop_summary_mode = configurable.get("stop_summary") is True
    sandbox_file_downloads = _sandbox_file_downloads_enabled(configurable)
    observability_tools: list[Any] = []
    corridor_tools: list[Any] = []
    browser_tools: list[Any] = []
    currents_tools: list[Any] = []
    notion_tools: list[Any] = []
    if not stop_summary_mode and not local_run:
        async with aphase(thread_id, "factory.observability_tools"):
            observability_authorized = await _observability_authorized(config, profile_login)
            if observability_authorized:
                observability_tools = await _load_observability_tools(True, profile_login)
            elif await _allowed_org_member(config, profile_login):
                observability_tools = await load_langsmith_tools(profile_login)
            else:
                observability_tools = await load_langsmith_tools(profile_login, allow_team=False)
        async with aphase(thread_id, "factory.corridor_tools"):
            corridor_tools = await _load_corridor_mcp_tools()
        browser_tools = load_browser_tools()

        if profile_login:
            async with aphase(thread_id, "factory.integration_tools"):
                currents_tools, notion_tools = await asyncio.gather(
                    _cached_tool_loader(
                        f"tools:currents:{profile_login}",
                        300,
                        lambda: load_currents_tools(profile_login),
                    ),
                    _cached_tool_loader(
                        f"tools:notion:{profile_login}",
                        300,
                        lambda: load_notion_tools(profile_login),
                    ),
                )

    slack_tools = [
        slack_add_reaction,
        slack_move_thread,
        slack_read_thread_messages,
        slack_start_new_thread,
        slack_thread_reply,
    ]
    static_tools = [
        http_request,
        fetch_url,
        web_search,
        approve_plan,
        background_execute,
        background_task,
        enter_plan_mode,
        save_plan,
        save_user_instructions,
        save_user_skill,
        delete_user_skill,
        linear_comment,
        linear_create_issue,
        linear_delete_issue,
        linear_get_issue,
        linear_get_issue_comments,
        linear_list_teams,
        linear_search_issues,
        linear_update_issue,
        list_threads,
        get_thread,
        manage_thread,
        manage_baby_sit,
        notify_automation_channel,
        open_pull_request,
        *((output_iframe, create_sandbox_file_download_url) if sandbox_file_downloads else ()),
        read_user_settings,
        request_pr_review,
        recreate_sandbox,
        report_platform_issue,
        schedule_thread_wakeup,
        slack_add_reaction,
        slack_move_thread,
        slack_read_thread_messages,
        slack_start_new_thread,
        slack_thread_reply,
        *(ADMIN_TOOLS if admin_thread else ()),
    ]
    if local_run:
        static_tools = [http_request, fetch_url, web_search]
    elif stop_summary_mode:
        static_tools = [slack_read_thread_messages, slack_thread_reply]
    reserved_tool_names = {_registered_tool_name(tool) for tool in static_tools}
    if not _slack_tools_enabled(configurable):
        static_tools = [tool for tool in static_tools if tool not in slack_tools]
    dynamic_tool_middleware: DynamicToolMiddleware | None = None
    integration_tool_groups = {
        "Corridor": corridor_tools,
        "Observability": observability_tools,
        "Currents": currents_tools,
        "Notion": notion_tools,
    }
    if any(integration_tool_groups.values()):
        dynamic_tool_middleware = DynamicToolMiddleware(
            integration_tool_groups,
            reserved_names={*DEEP_AGENT_TOOL_NAMES, *reserved_tool_names},
        )

    logger.info("Returning agent with sandbox for thread %s", thread_id)
    agent_backend: BackendProtocol = backend
    skill_routes: dict[str, BackendProtocol] = {
        BUNDLED_SKILLS_ROUTE: ReadOnlyBackend(
            FilesystemBackend(root_dir=BUNDLED_SKILLS_DIR, virtual_mode=True)
        ),
    }
    if is_desktop_run(configurable):
        skill_routes[USER_SKILLS_ROUTE] = ReadOnlyBackend(StateBackend())
        skill_sources = [USER_SKILLS_ROUTE, BUNDLED_SKILLS_ROUTE]
        # The default backend is the user's project, so offloads would land in
        # their repository. Keep the agent's scratch files out of it.
        skill_routes.update(await desktop_artifact_routes(thread_id))
    else:
        skill_routes[ORGANIZATION_SKILLS_ROUTE] = ReadOnlyBackend(
            StoreBackend(namespace=lambda _runtime: (ORGANIZATION_SKILLS_NAMESPACE,))
        )
        skill_sources = [ORGANIZATION_SKILLS_ROUTE, BUNDLED_SKILLS_ROUTE]
        if profile_login:
            skill_routes[USER_SKILLS_ROUTE] = ReadOnlyBackend(
                StoreBackend(
                    namespace=lambda _runtime, login=profile_login: (SKILLS_NAMESPACE, login)
                )
            )
            skill_sources.insert(0, USER_SKILLS_ROUTE)
    agent_backend = CompositeBackend(default=backend, routes=skill_routes)
    main_model = _make_model_or_defer(model_id, use_gateway=use_gateway, **model_kwargs)
    subagent_model = _make_model_or_defer(
        subagent_model_id,
        use_gateway=use_gateway,
        **subagent_model_kwargs,
    )
    subagent_tools = [
        tool
        for tool in static_tools
        if tool is not background_execute and tool is not background_task
    ]
    title_model = _make_model_or_defer(
        title_model_id,
        use_gateway=use_gateway,
        **title_model_kwargs,
    )
    return create_deep_agent(
        model=main_model,
        system_prompt="",
        tools=static_tools,
        subagents=[
            _general_purpose_subagent(
                subagent_model,
                tools=subagent_tools,
                skills=skill_sources,
                dynamic_tools=dynamic_tool_middleware,
                sandbox_file_downloads=sandbox_file_downloads,
            ),
            *([_browser_subagent(subagent_model, browser_tools)] if browser_tools else []),
        ],
        skills=skill_sources,
        backend=agent_backend,
        middleware=cast(
            list[AgentMiddleware[Any, Any, Any]],
            [
                PrepareAgentRunMiddleware(
                    thread_id=thread_id,
                    config=config,
                    profile_login=profile_login,
                    repo_instructions=repo_instructions,
                    model_id=model_id,
                    effort=profile_effort,
                    title_model=title_model,
                    source=source,
                    user_email=user_email,
                    linear_project_id=linear_project_id,
                    linear_issue_number=linear_issue_number,
                    draft_prs=sender_draft_prs,
                    plan_mode=plan_mode,
                    corridor_enabled=bool(corridor_tools),
                    admin_environments=admin_thread,
                ),
                *([dynamic_tool_middleware] if dynamic_tool_middleware else []),
                SanitizeToolInputsMiddleware(),
                ModelCallLimitMiddleware(run_limit=MODEL_CALL_RECURSION_LIMIT, exit_behavior="end"),
                ToolErrorMiddleware(),
                ExcludeToolsMiddleware(
                    excluded=(
                        STOP_SUMMARY_EXCLUDED_TOOLS
                        if stop_summary_mode
                        else DEEP_AGENT_EXCLUDED_TOOLS
                    )
                ),
                SubdirAgentsReadMiddleware(),
                ToolRetryMiddleware(
                    max_retries=2,
                    tools=["task"],
                    retry_on=task_retry_on,
                    on_failure=task_on_failure,
                    initial_delay=1.0,
                    max_delay=10.0,
                ),
                *([] if local_run else [PullRequestCreationGuardMiddleware()]),
                WorkflowPushGuardMiddleware(),
                refresh_github_proxy_before_model,
                *([] if stop_summary_mode else [check_message_queue_before_model]),
                TimeoutWrapupMiddleware(),
                DynamicContextMiddleware(),
                notify_step_limit_reached,
                *fallback_middleware,
                *plan_mode_middleware,
                SanitizeFireworksMessagesMiddleware(),
                SanitizeOpenAIResponsesMiddleware(),
                SanitizeThinkingBlocksMiddleware(),
                # Innermost, so the deadline covers the provider call itself and a
                # timeout escalates outward to the fallback model.
                ModelCallTimeoutMiddleware(),
            ],
        ),
    ).with_config(config)


traced_agent = traced_graph_factory(get_agent, AGENT_TRACING_PROJECT)
