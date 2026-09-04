"""Main entry point and graph factory for the Open SWE agent.

Resolves the model, ensures one sandbox per thread (simplified
get-or-create-then-reconnect, no cross-process ``__creating__`` sentinel),
builds the curated tool list plus optional integrations, and wires the
middleware stack. All per-thread state lives in the sandbox + thread metadata;
the agent itself is stateless.
"""
# ruff: noqa: E402

import hashlib
import logging
import os
import warnings
from collections.abc import Mapping, Sequence
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
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from agent.auth.resolve import resolve_github_token
from agent.sandboxes.lifecycle import (
    ensure_sandbox_for_thread,
    get_cached_sandbox_backend,
)
from agent.sandboxes.paths import resolve_sandbox_work_dir
from agent.sandboxes.read_only_backend import ReadOnlyBackend
from agent.sandboxes.state import (
    SandboxUnreachableError,
    get_or_create_sandbox_backend_proxy,
)

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
    resolve_environment,
)
from .dashboard.options import (
    SUPPORTED_MODEL_IDS,
    canonical_model_pair,
    gate_fable_model,
    model_supports_effort,
)
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
from .desktop_branch import schedule_worktree_branch_rename
from .input_messages import (
    SystemIdentity,
    build_input_messages,
    dynamic_context_hash,
    message_sender_id,
    system_introduction,
    visible_dynamic_context_hashes,
)
from .integrations.corridor_mcp import (
    CORRIDOR_TOOL_NAMES,
    corridor_configured,
    load_corridor_tools,
)
from .integrations.currents_tools import load_currents_tools
from .integrations.datadog_mcp import load_datadog_tools
from .integrations.langsmith_tools import load_langsmith_tools
from .integrations.notion_mcp import load_notion_tools
from .integrations.stagehand_browser import load_browser_tools
from .middleware import (
    BasePrepareRunMiddleware,
    DynamicToolMiddleware,
    ExcludeToolsMiddleware,
    IntegrationGroup,
    ModelCallTimeoutMiddleware,
    ModelFallbackMiddleware,
    PlanModeMiddleware,
    PullRequestCreationGuardMiddleware,
    SanitizeFireworksMessagesMiddleware,
    SanitizeOpenAIResponsesMiddleware,
    SanitizeThinkingBlocksMiddleware,
    SanitizeToolInputsMiddleware,
    StableToolResultOrderMiddleware,
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
    create_automation,
    create_sandbox_file_download_url,
    create_sandbox_service_url,
    delete_automation,
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
    list_automations,
    list_environments,
    list_threads,
    manage_baby_sit,
    manage_code_channel,
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
    slack_attach_html,
    slack_move_thread,
    slack_read_thread_messages,
    slack_start_new_thread,
    slack_thread_reply,
    trigger_automation,
    update_automation,
    web_search,
)
from .utils import ttl_cache
from .utils.authorship import (
    CollaboratorIdentity,
    resolve_participant_identities,
    resolve_triggering_user_identity,
)
from .utils.dashboard_links import dashboard_base_url, dashboard_plan_url, dashboard_thread_url
from .utils.deferred_model import make_deferred_error_model
from .utils.gateway import gateway_env_default
from .utils.github_org_membership import is_user_active_org_member
from .utils.json_types import as_json_object, thread_metadata
from .utils.model import (
    DEFAULT_LLM_REASONING,
    ModelKwargs,
    fallback_model_id_for,
    make_model,
    provider_model_kwargs,
)
from .utils.startup_trace import aphase
from .utils.thread_participants import PARTICIPANT_LOGINS_KEY, participant_logins
from .utils.thread_settings import (
    ThreadSettings,
    load_thread_settings,
    normalize_thread_settings,
    store_thread_settings,
)
from .utils.tracing import AGENT_TRACING_PROJECT, traced_graph_factory

client = get_client()

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


async def _thread_participant_identities(thread_id: str) -> list[CollaboratorIdentity]:
    """Git identities of everyone who has posted in this thread."""
    try:
        thread = await client.threads.get(thread_id=thread_id)
        logins = participant_logins(thread_metadata(thread).get(PARTICIPANT_LOGINS_KEY))
        return await resolve_participant_identities(logins)
    except Exception:
        logger.debug("Failed to resolve participant identities for %s", thread_id, exc_info=True)
        return []


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
        "browser_act",
        "browser_extract",
        "browser_navigate",
        "browser_observe",
        "create_sandbox_service_url",
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
        "create_automation",
        "update_automation",
        "trigger_automation",
        "delete_automation",
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
        "manage_code_channel",
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


_SENDER_CONTEXT_SYSTEM: SystemIdentity = {
    "id": "system:sender-context",
    "display_name": "Sender context",
    "platform": "open-swe",
}


# Added to an admin thread's tools; see the admin-thread section of the prompt.
ADMIN_TOOLS = (
    sandbox_reset,
    list_automations,
    create_automation,
    update_automation,
    trigger_automation,
    delete_automation,
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


async def _cached_allowed_org_member(config: RunnableConfig, profile_login: str | None) -> bool:
    login = _org_member_login(config, profile_login)
    if not login:
        return False
    return await ttl_cache.cached(
        f"org-member:{login}",
        300,
        lambda: _allowed_org_member(config, profile_login),
    )


def _org_member_login(config: RunnableConfig, profile_login: str | None) -> str | None:
    configurable = (config or {}).get("configurable") or {}
    config_login = configurable.get("github_login")
    return profile_login or (config_login if isinstance(config_login, str) else None)


async def _allowed_org_member(config: RunnableConfig, profile_login: str | None) -> bool:
    login = _org_member_login(config, profile_login)
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


async def _cached_langsmith_tools(profile_login: str | None, *, allow_team: bool) -> list[Any]:
    scope = "team" if allow_team else "solo"
    return await _cached_tool_loader(
        f"tools:langsmith:{profile_login or '-'}:{scope}",
        300,
        lambda: load_langsmith_tools(profile_login, allow_team=allow_team),
    )


async def _load_observability_tools(authorized: bool, profile_login: str | None) -> list[Any]:
    """Load team observability tools for an authorized triggering user."""
    if not authorized:
        return []
    datadog_tools, langsmith_tools = await asyncio.gather(
        _cached_tool_loader("tools:datadog", 600, load_datadog_tools),
        _cached_langsmith_tools(profile_login, allow_team=True),
    )
    return [*datadog_tools, *langsmith_tools]


async def _observability_tools_for(config: RunnableConfig, profile_login: str | None) -> list[Any]:
    """Observability tools the triggering user is allowed to see.

    The authorization gate itself stays uncached — it reads per-run config — so
    only the credential and membership lookups behind it are reused.
    """
    if await _observability_authorized(config, profile_login):
        return await _load_observability_tools(True, profile_login)
    if await _cached_allowed_org_member(config, profile_login):
        return await _cached_langsmith_tools(profile_login, allow_team=True)
    return await _cached_langsmith_tools(profile_login, allow_team=False)


async def _load_integration_tools(profile_login: str | None) -> tuple[list[Any], list[Any]]:
    if not profile_login:
        return [], []
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
    return currents_tools, notion_tools


async def _phase_result(thread_id: str | None, name: str, loader: Any) -> Any:
    async with aphase(thread_id, name):
        return await loader()


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
    def _sender_context_messages(state: PrepareRunState, sender_context: str) -> list[Any]:
        """Sender context as its own message, appended after the run's input.

        Splicing it into the triggering message rewrote history: that message is
        already cached from the run that received it, so every later run sent a
        different byte sequence for it. The transcript renders one envelope per
        message, so this arrives as a collapsed context pill rather than markup
        inside the user's own text.
        """
        if not any(
            isinstance(candidate, HumanMessage) for candidate in state.get("messages") or []
        ):
            return []
        sender_id = next(
            (
                sender_id
                for candidate in reversed(state.get("messages") or [])
                if isinstance(candidate, HumanMessage)
                and (sender_id := message_sender_id(candidate.content, kind="human")) is not None
            ),
            None,
        )
        if sender_id is None:
            return []
        identity: SystemIdentity = {
            **_SENDER_CONTEXT_SYSTEM,
            "subject_id": sender_id,
            "context_hash": hashlib.sha256(sender_context.encode()).hexdigest(),
        }
        introduction_hash = dynamic_context_hash(system_introduction(identity)["content"])
        if introduction_hash in visible_dynamic_context_hashes(state):
            return []
        return cast(
            list[Any],
            build_input_messages(
                sender_context,
                {
                    "sender_id": _SENDER_CONTEXT_SYSTEM["id"],
                    "surface": "automation",
                    "kind": "system",
                },
                systems=[identity],
            ),
        )

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
            local_path = configurable.get("local_project_path")
            if isinstance(local_path, str) and local_path:
                schedule_worktree_branch_rename(
                    worktree_path=local_path,
                    messages=state.get("messages") or [],
                    model=self._title_model,
                )
            async with aphase(self._thread_id, "prepare.await_sandbox"):
                sandbox_backend = await get_or_create_sandbox_backend_proxy(self._thread_id).ready()
            async with aphase(self._thread_id, "prepare.work_dir"):
                work_dir = await resolve_sandbox_work_dir(sandbox_backend)
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
            work_dir = await resolve_sandbox_work_dir(sandbox_backend)
        async with aphase(self._thread_id, "prepare.environment"):
            environment = await resolve_environment(_environment_slug(configurable))
        async with aphase(self._thread_id, "prepare.sender_context"):
            sender_instructions, participant_identities = await asyncio.gather(
                _resolve_user_custom_instructions(self._profile_login),
                _thread_participant_identities(self._thread_id),
            )
            sender_context = construct_sender_context(
                triggering_user_identity,
                user_custom_instructions=sender_instructions,
                draft_prs=self._draft_prs,
                thread_url=dashboard_thread_url(self._thread_id),
                workspace_admin=await _workspace_admin(self._config or {}, self._profile_login),
                participant_identities=participant_identities,
            )
        sender_messages = self._sender_context_messages(state, sender_context)
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
            **({"messages": sender_messages} if sender_messages else {}),
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
                environment_name=environment.name if environment else None,
                environment_instructions=environment.instructions if environment else None,
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
        return await ensure_sandbox_for_thread(
            _thread_id,
            environment_slug=_environment_slug(_configurable),
        )

    backend = get_cached_sandbox_backend(thread_id, reconnect=reconnect_backend)
    backend.start()

    # `profile_login` is whoever sent the message that started this run; it drives
    # authorization and credentialed integrations, which stay personal to them.
    # Everything else comes from the thread's own settings, seeded from the first
    # sender's profile and frozen there afterwards.
    local_run = is_desktop_run(configurable)
    profile_login = resolve_github_login(as_json_object(config))
    async with aphase(thread_id, "factory.thread_settings"):
        thread_settings, settings_changed = normalize_thread_settings(
            {} if local_run else await load_thread_settings(client, thread_id)
        )
    # Team/profile settings are accepted stale for a short TTL so graph factories
    # stay off the critical path during worker load and retry storms.
    if local_run:
        from .dashboard.options import default_model_pair

        team_defaults = (default_model_pair(), default_model_pair())
        title_defaults = team_defaults[0]
        use_gateway = gateway_env_default()
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
                _cached_profile(None if thread_settings.get("model_id") else profile_login),
                _cached_fable_enabled(),
            )

    linear_issue = as_json_object(configurable.get("linear_issue"))
    linear_project_id = linear_issue.get("linear_project_id", "")
    linear_issue_number = linear_issue.get("linear_issue_number", "")

    (model_id, profile_effort), (subagent_model_id, subagent_effort) = team_defaults
    title_model_id, title_effort = title_defaults
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
        sender_profile = profile if profile is not None else await _cached_profile(profile_login)
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
    currents_tools: list[Any] = []
    notion_tools: list[Any] = []
    if not stop_summary_mode and not local_run:
        observability_tools, (currents_tools, notion_tools) = await asyncio.gather(
            _phase_result(
                thread_id,
                "factory.observability_tools",
                lambda: _observability_tools_for(config, profile_login),
            ),
            _phase_result(
                thread_id,
                "factory.integration_tools",
                lambda: _load_integration_tools(profile_login),
            ),
        )

    slack_tools = [
        manage_code_channel,
        slack_add_reaction,
        slack_attach_html,
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
        *(
            (output_iframe, create_sandbox_file_download_url, create_sandbox_service_url)
            if sandbox_file_downloads
            else ()
        ),
        read_user_settings,
        request_pr_review,
        recreate_sandbox,
        report_platform_issue,
        schedule_thread_wakeup,
        manage_code_channel,
        slack_add_reaction,
        slack_attach_html,
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
    integration_tool_groups: dict[str, IntegrationGroup | Sequence[Any]] = {
        "Observability": observability_tools,
        "Currents": currents_tools,
        "Notion": notion_tools,
    }
    if not stop_summary_mode and not local_run:
        browser_tools = load_browser_tools()
        if browser_tools:
            integration_tool_groups["Browser"] = browser_tools
    # Corridor's catalog is a static allowlist, so the MCP handshake that used to
    # run before every first model call now waits until the agent asks for it.
    if not stop_summary_mode and not local_run and corridor_configured():
        integration_tool_groups["Corridor"] = IntegrationGroup(
            tool_names=CORRIDOR_TOOL_NAMES,
            load=_load_corridor_mcp_tools,
        )
    if integration_tool_groups:
        candidate = DynamicToolMiddleware(
            integration_tool_groups,
            reserved_names={*DEEP_AGENT_TOOL_NAMES, *reserved_tool_names},
        )
        if candidate.has_groups:
            dynamic_tool_middleware = candidate

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
                    corridor_enabled="Corridor" in integration_tool_groups,
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
                notify_step_limit_reached,
                *fallback_middleware,
                *plan_mode_middleware,
                SanitizeFireworksMessagesMiddleware(),
                SanitizeOpenAIResponsesMiddleware(),
                SanitizeThinkingBlocksMiddleware(),
                StableToolResultOrderMiddleware(),
                # Innermost, so the deadline covers the provider call itself and a
                # timeout escalates outward to the fallback model.
                ModelCallTimeoutMiddleware(),
            ],
        ),
    ).with_config(config)


traced_agent = traced_graph_factory(get_agent, AGENT_TRACING_PROJECT)
