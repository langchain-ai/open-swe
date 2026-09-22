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
import warnings
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from agent.config import ENV

logger = logging.getLogger(__name__)

_MODEL_ROUTING_SPLIT = 0.5

from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel
from langgraph.runtime import Runtime
from langgraph_sdk import get_client

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")

import asyncio
from dataclasses import replace

# Suppress Pydantic v1 compatibility warnings from langchain on Python 3.14+
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from deepagents import create_deep_agent
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import BackendProtocol, SandboxBackendProtocol
from deepagents.backends.state import StateBackend
from deepagents.backends.store import StoreBackend
from deepagents.graph import DeepAgentState
from deepagents.middleware.filesystem import FilesystemState
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolRetryMiddleware
from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command


class _DisableInheritedMiddleware(AgentMiddleware):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name


from agent.analytics.usage import record_agent_invocation_usage
from agent.credential_scope import private_credential_login
from agent.dashboard.agent_overrides import (
    load_profile,
    normalize_profile_overrides,
    normalize_profile_subagent_overrides,
    profile_draft_prs,
    profile_model_routing_enabled,
    resolve_github_login,
)
from agent.dashboard.options import (
    SUPPORTED_MODEL_IDS,
    canonical_model_pair,
    gate_fable_model,
    model_supports_effort,
)
from agent.dashboard.workspace_settings import WorkspaceSettings, get_workspace_settings
from agent.dashboard.workspace_settings_cache import cached_workspace_settings
from agent.desktop import create_desktop_backend, desktop_artifact_routes, is_desktop_run
from agent.desktop_branch import schedule_worktree_branch_rename
from agent.github.token import resolve_github_token
from agent.input_messages import (
    dynamic_context_hash,
    message_sender_id,
    person_introduction,
    visible_dynamic_context_hashes,
)
from agent.mcp import load_mcp_tools
from agent.mcp.instance import instance_mcp_source
from agent.mcp.user import user_mcp_source
from agent.mcp.workspace import workspace_mcp_source
from agent.middleware import (
    BasePrepareRunMiddleware,
    DynamicToolMiddleware,
    ExcludeToolsMiddleware,
    IntegrationGroup,
    ModelCallTimeoutMiddleware,
    ModelErrorMiddleware,
    ModelFallbackMiddleware,
    ModelSelectionMiddleware,
    PullRequestCreationGuardMiddleware,
    RequireUserReplyMiddleware,
    SanitizeFireworksMessagesMiddleware,
    SanitizeOpenAIResponsesMiddleware,
    SanitizeThinkingBlocksMiddleware,
    SanitizeToolInputsMiddleware,
    StableToolResultOrderMiddleware,
    SubdirAgentsReadMiddleware,
    TimeoutWrapupMiddleware,
    ToolErrorMiddleware,
    ValidateImageReadsMiddleware,
    WorkflowPushGuardMiddleware,
    WorkspaceSkillsMiddleware,
    check_message_queue_before_model,
    notify_step_limit_reached,
    record_run_usage,
    refresh_github_proxy_before_model,
    task_on_failure,
    task_retry_on,
)
from agent.middleware.conversation_offloading import ConversationOffloadingMiddleware
from agent.middleware.model_selection import ModelSelectionState, RoutingMode
from agent.middleware.prepare_run import PrepareRunState
from agent.middleware.require_user_reply import (
    SLACK_REPLY_SURFACE,
    WEB_REPLY_SURFACE,
    ReplySurface,
)
from agent.middleware.sandbox_circuit_breaker import post_sandbox_unreachable_notification
from agent.middleware.transcript import TranscriptMiddleware
from agent.prompt import construct_system_prompt
from agent.prompts import apply_tool_descriptions, load_prompt
from agent.run_config import RunConfig
from agent.runtime.constants import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_RECURSION_LIMIT,
    MODEL_CALL_RECURSION_LIMIT,
)
from agent.runtime.constants import (
    DEFAULT_LLM_MODEL_ID as DEFAULT_LLM_MODEL_ID,
)
from agent.runtime.execution import bindable_config, graph_loaded_for_execution
from agent.sandboxes.lifecycle import (
    ensure_sandbox_for_thread,
    get_cached_sandbox_backend,
)
from agent.sandboxes.paths import resolve_sandbox_work_dir
from agent.sandboxes.providers.langsmith import service_identity_jwks_url
from agent.sandboxes.read_only_backend import ReadOnlyBackend
from agent.sandboxes.state import (
    SandboxUnreachableError,
    get_or_create_sandbox_backend_proxy,
)
from agent.sandboxes.tool_access import tools_base_url
from agent.sandboxes.tool_runtime import ToolSurface, save_tool_context
from agent.skill_store.store import ORGANIZATION_SKILLS_NAMESPACE, SKILLS_NAMESPACE
from agent.slack.dm import is_dm_channel, is_dm_session
from agent.thread_title import TITLE_GENERATION_MAX_TOKENS, schedule_thread_title_generation
from agent.threads.recent_context import RecentContextAudience, recent_thread_context_section
from agent.threads.summary import DASHBOARD_SOURCE, thread_is_private
from agent.tool_loaders.notion_mcp import load_notion_tools
from agent.tools import (
    background_execute,
    background_task,
    create_automation,
    create_sandbox_file_download_url,
    delete_automation,
    delete_organization_skill,
    delete_user_skill,
    delete_workspace,
    expedite_pr_approval,
    expose_port,
    fetch_url,
    get_thread,
    http_request,
    list_automations,
    list_threads,
    list_workspaces,
    manage_baby_sit,
    manage_code_channel,
    manage_incident,
    manage_thread,
    notify_automation_channel,
    open_pull_request,
    output_iframe,
    publish_workspace,
    read_only_sql,
    read_user_settings,
    recreate_sandbox,
    refresh_workspace_start,
    report_platform_issue,
    request_pr_review,
    save_organization_skill,
    save_plan,
    save_user_instructions,
    save_user_settings,
    save_user_skill,
    schedule_thread_wakeup,
    slack_add_reaction,
    slack_attach_html,
    slack_list_channels,
    slack_move_thread,
    slack_no_reply_needed,
    slack_post_message,
    slack_read_channel_messages,
    slack_read_thread_messages,
    slack_reply,
    slack_start_new_thread,
    submit_thread_feedback,
    trigger_automation,
    update_automation,
    web_search,
)
from agent.tools.admin_gate import (
    actor_has_admin_context,
    actor_is_admin,
    is_private_admin_surface,
    participant_is_admin,
)
from agent.tools.manage_review_approval_policy import manage_review_approval_policy
from agent.tools.save_user_settings import personal_settings_run_allowed
from agent.tools.submit_review_assessment_feedback import submit_review_assessment_feedback
from agent.users import User
from agent.utils import ttl_cache
from agent.utils.authorship import (
    OPEN_SWE_BOT_EMAIL,
    OPEN_SWE_BOT_NAME,
    CollaboratorIdentity,
    ThreadParticipant,
    resolve_participant_identities,
    resolve_triggering_user_identity,
)
from agent.utils.dashboard_links import dashboard_base_url, dashboard_plan_url
from agent.utils.deferred_model import make_deferred_error_model
from agent.utils.gateway import gateway_env_default
from agent.utils.json_types import as_json_object, thread_metadata
from agent.utils.model import (
    DEFAULT_LLM_REASONING,
    ModelKwargs,
    fallback_model_id_for,
    make_model,
    provider_model_kwargs,
)
from agent.utils.startup_trace import aphase
from agent.utils.thread_participants import PARTICIPANT_LOGINS_KEY, participant_logins
from agent.utils.thread_settings import (
    ThreadSettings,
    load_thread_settings,
    normalize_thread_settings,
    store_thread_settings,
)
from agent.workspaces.store import (
    DEFAULT_WORKSPACE_SLUG,
    load_workspace,
)

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
# A `/oswe` request has a channel but no Slack thread, so only the tools that act
# on one are out of reach. Everything else, writes included, stays available.
SLACK_ASK_EXCLUDED_TOOLS = DEEP_AGENT_EXCLUDED_TOOLS | frozenset(
    {
        "manage_code_channel",
        "manage_incident",
        "slack_add_reaction",
        "slack_attach_html",
        "slack_move_thread",
    }
)


# Reading a Slack channel takes an explicit channel id and nothing from the run's
# source context, so it survives every gate the thread-bound Slack tools do not.
SOURCE_FREE_SLACK_TOOLS: frozenset[str] = frozenset({"slack_read_channel_messages"})


def _registered_tool_name(value: Any) -> str:
    name = getattr(value, "name", None) or getattr(value, "__name__", None)
    if not isinstance(name, str) or not name:
        raise TypeError(f"tool has no registered name: {value!r}")
    return name


def _tool_loader_timeout_seconds() -> float:
    raw_timeout = ENV.TOOL_LOADER_TIMEOUT_SECONDS.optional()
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


async def _resolve_prompt_default_repo(cfg: RunConfig) -> dict[str, str] | None:
    if cfg.repo:
        return {"owner": cfg.repo.owner, "name": cfg.repo.name}

    if cfg.repo_explicitly_none is True:
        return None

    try:
        return (await get_workspace_settings(workspace_slug(cfg))).default_repo
    except Exception:
        logger.debug("Failed to load the workspace default repo for prompt", exc_info=True)
        return None


async def _resolve_repo_custom_instructions(
    default_repo: dict[str, str] | None,
) -> str | None:
    """Load per-repo custom agent instructions for the resolved default repo."""
    if not default_repo or not default_repo.get("owner") or not default_repo.get("name"):
        return None
    try:
        from agent.dashboard.agent_instructions import get_repo_agent_instructions

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


async def _user_for_login(login: str) -> User | None:
    """The ``users`` row behind a GitHub login, or ``None`` when nothing answers."""
    try:
        return await User.for_login("github", login)
    except Exception:
        logger.warning(
            "Could not resolve a participant; describing them from surface data",
            extra={"participant_login": login},
            exc_info=True,
        )
        return None


async def _thread_participant(
    identity: CollaboratorIdentity,
    config: RunnableConfig,
    *,
    person_id: str | None = None,
    timezone: str = "",
) -> ThreadParticipant:
    login = identity.github_login or None
    if login is None:
        return ThreadParticipant(identity=identity, person_id=person_id or "", timezone=timezone)
    user, profile, workspace_admin, instructions = await asyncio.gather(
        _user_for_login(login),
        load_profile(login),
        participant_is_admin(login),
        _resolve_user_custom_instructions(login),
    )
    display_name = (user.display_name if user else "") or identity.display_name or login
    return ThreadParticipant(
        identity=replace(
            identity,
            display_name=display_name,
            commit_name=display_name,
            commit_email=identity.login_noreply_email,
        ),
        person_id=person_id or (f"user:{user.id}" if user else f"github:{login}"),
        workspace_admin=workspace_admin,
        draft_prs=profile_draft_prs(profile),
        instructions=instructions or "",
        email=(user.email if user else "") or "",
        timezone=timezone,
        linked=user is not None,
    )


async def _thread_participants(
    thread_id: str,
    config: RunnableConfig,
    sender: CollaboratorIdentity | None,
    *,
    sender_person_id: str,
    sender_display_name: str = "",
    sender_timezone: str = "",
) -> list[ThreadParticipant]:
    """Everyone in the thread, each with the settings the agent acts under for them.

    The sender is keyed by the id their message envelope carries, so the turn's
    envelope resolves to their block even when no person row exists. Without a
    GitHub account they have no commit identity, and the surface's name for them
    is all anyone knows.
    """
    identities = await _thread_participant_identities(thread_id)
    resolved_sender = sender or (
        CollaboratorIdentity(display_name=sender_display_name, commit_name="", commit_email="")
        if sender_display_name
        else CollaboratorIdentity(
            display_name=OPEN_SWE_BOT_NAME,
            commit_name=OPEN_SWE_BOT_NAME,
            commit_email=OPEN_SWE_BOT_EMAIL,
        )
    )
    others = [
        identity for identity in identities if identity.commit_email != resolved_sender.commit_email
    ]
    return list(
        await asyncio.gather(
            _thread_participant(
                resolved_sender, config, person_id=sender_person_id, timezone=sender_timezone
            ),
            *(_thread_participant(identity, config) for identity in others),
        )
    )


async def _resolve_user_custom_instructions(login: str | None) -> str | None:
    """Load user-level custom agent instructions for the triggering user."""
    if not login:
        return None
    try:
        from agent.dashboard.user_instructions import get_user_custom_instructions

        return await get_user_custom_instructions(login)
    except Exception:
        logger.debug("Failed to load user custom agent instructions", exc_info=True)
        return None


INCIDENT_AUTOMATIC_EXCLUDED_TOOLS: frozenset[str] = frozenset(
    {
        "manage_code_channel",
        "manage_incident",
        "slack_add_reaction",
        "slack_attach_html",
        "slack_reply",
        "task",
        "background_execute",
        "background_task",
        "expose_port",
        "http_request",
        "expedite_pr_approval",
        "manage_baby_sit",
        "manage_thread",
        "open_pull_request",
        "recreate_sandbox",
        "request_pr_review",
        "save_user_skill",
        "delete_user_skill",
        "slack_move_thread",
        "slack_post_message",
        "slack_start_new_thread",
        "publish_workspace",
        "refresh_workspace_start",
        "delete_workspace",
        "create_automation",
        "update_automation",
        "trigger_automation",
        "delete_automation",
    }
)

# A reaction signals "seen, working on it" to a room. A DM is a two-person
# conversation where the reply itself is that signal, so reacting there is only
# clutter on every message the person sends.
DM_EXCLUDED_TOOLS: frozenset[str] = frozenset({"slack_add_reaction"})


def _subagent_model_middleware() -> list[AgentMiddleware[Any, Any, Any]]:
    """Provider guards for subagent model calls.

    Subagents compile into their own graphs, so parent middleware never wraps them.
    """
    return cast(
        list[AgentMiddleware[Any, Any, Any]],
        [
            SanitizeOpenAIResponsesMiddleware(),
            ModelErrorMiddleware(),
            ModelCallTimeoutMiddleware(),
        ],
    )


def _subagent_middleware(
    dynamic_tools: DynamicToolMiddleware | None,
) -> list[AgentMiddleware[Any, Any, Any]]:
    middleware: list[AgentMiddleware[Any, Any, Any]] = []
    if dynamic_tools is not None:
        middleware.append(dynamic_tools)
    middleware.append(WorkflowPushGuardMiddleware())
    middleware.extend(_subagent_model_middleware())
    return middleware


def _subagent_guard_middleware(local_run: bool) -> list[AgentMiddleware[Any, Any, Any]]:
    """Shell guards mirroring the parent stack for delegated tool calls.

    Local desktop runs skip the PR-creation guard the same way the parent does.
    """
    if local_run:
        return []
    return [PullRequestCreationGuardMiddleware()]


def _is_subagent_excluded_tool(name: str) -> bool:
    """Return whether a tool requires the parent agent."""
    if name in SOURCE_FREE_SLACK_TOOLS:
        return False
    return name.startswith("slack_") or name in {
        "background_execute",
        "background_task",
        "submit_thread_feedback",
        "submit_review_assessment_feedback",
        "get_thread",
        "manage_code_channel",
        "manage_incident",
        "list_threads",
        "manage_thread",
        "notify_automation_channel",
        "read_incident",
        "read_only_sql",
        "read_user_settings",
        "save_user_settings",
        "record_incident_report",
        "search_incidents",
    }


class _SubagentToolGuard(AgentMiddleware):
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if _is_subagent_excluded_tool(request.tool_call["name"]):
            return ToolMessage(
                content=load_prompt("tools/subagent-unavailable.md"),
                tool_call_id=request.tool_call["id"],
            )
        return await handler(request)


def _general_purpose_subagent(
    model: BaseChatModel,
    tools: Sequence[Any],
    dynamic_tools: DynamicToolMiddleware | None = None,
    *,
    offloading: ConversationOffloadingMiddleware | None = None,
    workspace_skills: WorkspaceSkillsMiddleware | None = None,
    incident_middleware: AgentMiddleware | None = None,
    guard_middleware: Sequence[AgentMiddleware[Any, Any, Any]] = (),
    inherited_middleware_exclusions: Sequence[str] = (),
) -> SubAgent:
    subagent: SubAgent = {
        "name": GENERAL_PURPOSE_SUBAGENT["name"],
        "description": (
            f"{GENERAL_PURPOSE_SUBAGENT['description']} "
            f"{load_prompt('system/general-purpose-subagent-suffix.md')}"
        ),
        "mode": "fork",
        "model": model,
        "tools": list(tools),
        "middleware": cast(
            list[AgentMiddleware[Any, Any, Any]],
            [
                *(_DisableInheritedMiddleware(name) for name in inherited_middleware_exclusions),
                _SubagentToolGuard(),
                TranscriptMiddleware(),
                *([incident_middleware] if incident_middleware else []),
                *([workspace_skills] if workspace_skills else []),
                *_subagent_middleware(dynamic_tools),
                *guard_middleware,
                *([offloading] if offloading else []),
            ],
        ),
    }
    return subagent


# Added to an admin thread's tools; see the admin-thread section of the prompt.
ADMIN_TOOLS = (
    list_automations,
    create_automation,
    update_automation,
    trigger_automation,
    delete_automation,
    list_workspaces,
    publish_workspace,
    refresh_workspace_start,
    delete_workspace,
    save_organization_skill,
    delete_organization_skill,
)


def workspace_slug(cfg: RunConfig) -> str | None:
    """The workspace this thread selected, if any."""
    return cfg.workspace_slug


async def _workspace_admin(config: RunnableConfig, profile_login: str | None) -> bool:
    return await actor_is_admin(RunConfig.from_config(config), login=profile_login)


async def _admin_thread(config: RunnableConfig, profile_login: str | None) -> bool:
    """Whether this run may manage workspaces and organization skills."""
    return await actor_has_admin_context(RunConfig.from_config(config), login=profile_login)


async def _private_thread(thread_id: str | None) -> bool:
    """Whether only this thread's owner can read it. Fails closed."""
    if not thread_id:
        return False
    try:
        thread = await client.threads.get(thread_id=thread_id)
    except Exception:
        logger.debug("Could not read visibility for thread %s", thread_id, exc_info=True)
        return False
    return thread_is_private(thread_metadata(thread))


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


async def _notion_tools_for(profile_login: str | None) -> list[Any]:
    if not profile_login:
        return []
    return await _cached_tool_loader(
        f"tools:notion:{profile_login}",
        300,
        lambda: load_notion_tools(profile_login),
    )


async def _mcp_tools_for(credential_login: str | None, workspace: str) -> list[Any]:
    """Load the run's MCPs by tier: instance, then workspace, then the user's own.

    A later tier's connection replaces a same-named one from the tier before.
    """
    sources = [instance_mcp_source(), workspace_mcp_source(workspace)]
    if credential_login:
        sources.append(user_mcp_source(credential_login))
    return await load_mcp_tools(*sources)


async def _phase_result(thread_id: str | None, name: str, loader: Any) -> Any:
    async with aphase(thread_id, name):
        return await loader()


async def _cached_profile(profile_login: str | None):
    if not profile_login:
        return None
    return await ttl_cache.cached(
        f"profile:{profile_login}", 30, lambda: load_profile(profile_login)
    )


def _sandbox_file_downloads_enabled(cfg: RunConfig) -> bool:
    """Return whether signed sandbox file downloads are available for this run."""
    return (
        ENV.SANDBOX_TYPE.get() == "langsmith"
        and cfg.stop_summary is not True
        and not is_desktop_run(cfg)
    )


def _slack_tools_enabled(cfg: RunConfig) -> bool:
    """Return whether the run has trusted Slack source context.

    A web follow-up counts: its `slack_thread` is copied from the thread's own
    metadata, never from the client, and keeping the tools registered across a
    surface switch is what keeps the prompt prefix cacheable.
    """
    if cfg.source not in {"slack", "schedule", "incidents_agent", DASHBOARD_SOURCE}:
        return False
    if cfg.slack_thread is None:
        return False
    if _slack_ask_mode(cfg):
        return bool(cfg.slack_thread.channel_id.strip())
    return bool(cfg.slack_thread.channel_id.strip() and cfg.slack_thread.thread_ts.strip())


def _initial_reply_surface(cfg: RunConfig) -> ReplySurface:
    """Where this run owes its answer, before anything moves mid-run."""
    if cfg.source == DASHBOARD_SOURCE or not _slack_tools_enabled(cfg):
        return WEB_REPLY_SURFACE
    return SLACK_REPLY_SURFACE


def _slack_ask_mode(cfg: RunConfig) -> bool:
    """A `/oswe` question: one ephemeral answer, no Slack thread to post into."""
    return (
        cfg.slack_ask is True
        and cfg.slack_thread is not None
        and bool(cfg.slack_thread.triggering_user_id.strip())
    )


def _slack_dm_run(cfg: RunConfig) -> bool:
    """Whether this run answers in a bot DM the owner runs as one session."""
    return (
        _slack_tools_enabled(cfg)
        and cfg.slack_thread is not None
        and is_dm_session(cfg.slack_thread.channel_context, cfg.slack_thread.thread_ts)
    )


def _model_routing_mode(thread_id: str) -> RoutingMode:
    digest = hashlib.sha256(thread_id.encode()).hexdigest()
    bucket = int(digest[:8], 16) / float(0xFFFF_FFFF)
    return "auto" if bucket < _MODEL_ROUTING_SPLIT else "performance"


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
        admin_workspaces: bool,
        model_selection: ModelSelectionMiddleware | None = None,
        routing_defaults: Mapping[str, tuple[str, str | None]] | None = None,
        credential_login: str | None = None,
    ) -> None:
        self._thread_id = thread_id
        self._config = config
        self._profile_login = profile_login
        self._credential_login = credential_login
        self._repo_instructions = repo_instructions
        self._model_id = model_id
        self._effort = effort
        self._title_model = title_model
        self._source = source
        self._user_email = user_email
        self._linear_project_id = linear_project_id
        self._linear_issue_number = linear_issue_number
        self._draft_prs = draft_prs
        self._admin_workspaces = admin_workspaces
        self._model_selection = model_selection
        self._routing_defaults = dict(routing_defaults or {})

    def _recent_context_audience(self, cfg: RunConfig) -> RecentContextAudience | None:
        if (
            cfg.background_task_completion
            or not self._profile_login
            or (cfg.slack_thread is not None and cfg.slack_thread.triggering_bot_id)
        ):
            return None
        private_owner = (self._credential_login or "").lower() == self._profile_login.lower()
        if self._source == "dashboard":
            return "private" if private_owner else None
        if self._source != "slack" or cfg.slack_thread is None:
            return None
        channel_context = cfg.slack_thread.channel_context
        if is_dm_channel(channel_context):
            return "private" if private_owner else None
        if (
            channel_context is not None
            and channel_context.is_im is False
            and channel_context.is_mpim is False
            and cfg.slack_thread.team_id
            and cfg.slack_thread.channel_id
        ):
            return "shared_slack"
        return None

    def _prepare_config_fingerprint(self) -> Any:
        cfg = RunConfig.from_config(self._config)
        return {
            "credential_login": self._credential_login,
            "invocation_id": cfg.invocation_id,
            "thread_id": self._thread_id,
            "source": self._source,
            "repo": cfg.repo.model_dump() if cfg.repo else None,
            "draft_prs": self._draft_prs,
            "model": self._model_id,
            "effort": self._effort,
        }

    @staticmethod
    def _sender_subject_id(state: PrepareRunState, sender_id: str | None) -> str | None:
        """The entity the sender context describes: the latest human message's sender."""
        if sender_id is not None:
            return sender_id
        return next(
            (
                candidate_id
                for candidate in reversed(state.get("messages") or [])
                if isinstance(candidate, HumanMessage)
                and (candidate_id := message_sender_id(candidate.content, kind="human")) is not None
            ),
            None,
        )

    @staticmethod
    def _participants_messages(
        state: PrepareRunState, participants: Sequence[ThreadParticipant]
    ) -> list[Any]:
        """One person block per participant, sent when theirs is not already visible."""
        visible = visible_dynamic_context_hashes(state)
        ordered = sorted(
            participants,
            key=lambda candidate: (candidate.identity.display_name.lower(), candidate.person_id),
        )
        blocks = [person_introduction(p.as_person()) for p in ordered]
        return [block for block in blocks if dynamic_context_hash(block["content"]) not in visible]

    async def _prepare(self, state: PrepareRunState, runtime: Runtime) -> dict[str, Any]:  # noqa: ARG002
        schedule_thread_title_generation(
            thread_id=self._thread_id,
            messages=state.get("messages") or [],
            model=self._title_model,
            client=client,
        )
        configurable = (self._config or {}).get("configurable") or {}
        configurable["draft_prs"] = self._draft_prs
        cfg = RunConfig.parse(configurable)
        if is_desktop_run(cfg):
            if cfg.local_project_path:
                schedule_worktree_branch_rename(
                    worktree_path=cfg.local_project_path,
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
            prompt_default_repo = await _resolve_prompt_default_repo(cfg)
        triggering_user_identity_task = asyncio.create_task(
            resolve_triggering_user_identity(as_json_object(self._config), github_token)
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
        async with aphase(self._thread_id, "prepare.workspace"):
            workspace = await load_workspace(workspace_slug(cfg))
        async with aphase(self._thread_id, "prepare.participants"):
            recent_context_audience = self._recent_context_audience(cfg)
            recent_context_task = (
                asyncio.create_task(
                    recent_thread_context_section(
                        audience=recent_context_audience,
                        login=self._profile_login,
                        email=self._user_email or None,
                        exclude_thread_id=self._thread_id,
                        slack_team_id=(cfg.slack_thread.team_id if cfg.slack_thread else None),
                        slack_channel_id=(
                            cfg.slack_thread.channel_id if cfg.slack_thread else None
                        ),
                    )
                )
                if recent_context_audience is not None
                else None
            )
            attribution_model_id = self._model_id
            attribution_effort = self._effort
            attribution_route = None
            if self._model_selection is not None:
                attribution_route = await self._model_selection.select_route(
                    cast(ModelSelectionState, state)
                )
                attribution_model_id, attribution_effort = self._routing_defaults[attribution_route]
            bot_id = (
                cfg.slack_thread.triggering_bot_id
                if self._source == "slack" and cfg.slack_thread
                else ""
            )
            subject_id = self._sender_subject_id(
                state, f"system:slack-bot-{bot_id}" if bot_id else None
            )
            sender_messages: list[Any] = []
            if subject_id is not None:
                participants = await _thread_participants(
                    self._thread_id,
                    self._config or {},
                    triggering_user_identity,
                    sender_person_id=subject_id,
                    sender_display_name=(
                        cfg.slack_thread.triggering_user_name if cfg.slack_thread else ""
                    ),
                    sender_timezone=(
                        cfg.slack_thread.triggering_user_timezone if cfg.slack_thread else ""
                    ),
                )
                sender_messages = self._participants_messages(state, participants)
        recent_thread_context = await recent_context_task if recent_context_task is not None else ""
        try:
            async with aphase(self._thread_id, "prepare.record_run"):
                await client.threads.update(
                    thread_id=self._thread_id,
                    metadata={
                        "agent_kind": "agent",
                        "model": attribution_model_id,
                        "effort": attribution_effort,
                        "source": self._source,
                        **({"model_route": attribution_route} if attribution_route else {}),
                    },
                )
                if cfg.invocation_id:
                    await record_agent_invocation_usage(
                        invocation_id=cfg.invocation_id,
                        thread_id=self._thread_id,
                        github_login=self._profile_login,
                        github_user_id=(
                            triggering_user_identity.github_user_id
                            if triggering_user_identity
                            and triggering_user_identity.github_user_id is not None
                            else cfg.github_user_id
                        ),
                        user_email=self._user_email,
                        display_name=(
                            triggering_user_identity.analytics_display_name
                            if triggering_user_identity
                            and triggering_user_identity.analytics_display_name
                            else None
                        ),
                        display_name_source=(
                            triggering_user_identity.display_name_source
                            if triggering_user_identity
                            else None
                        ),
                        model_id=attribution_model_id,
                        effort=attribution_effort,
                        source=self._source,
                        repository=cfg.repo_full_name or None,
                    )
        except Exception:
            logger.debug(
                "Failed to record agent usage for thread %s", self._thread_id, exc_info=True
            )

        return {
            "work_dir": work_dir,
            **({"messages": sender_messages} if sender_messages else {}),
            **({"model_route": attribution_route} if attribution_route else {}),
            "rendered_system_prompt": construct_system_prompt(
                working_dir=work_dir,
                dashboard_base_url=dashboard_base_url(),
                artifact_url=dashboard_plan_url(self._thread_id),
                linear_project_id=self._linear_project_id,
                linear_issue_number=self._linear_issue_number,
                default_repo=prompt_default_repo,
                repo_custom_instructions=self._repo_instructions,
                workspace_name=workspace.name if workspace else None,
                workspace_instructions=workspace.instructions if workspace else None,
                admin_workspaces=self._admin_workspaces,
                source="background_task" if cfg.background_task_completion else self._source,
                slack_context=_slack_tools_enabled(cfg),
                slack_ask=_slack_ask_mode(cfg),
                sandbox_file_downloads=_sandbox_file_downloads_enabled(cfg),
                continued_from_collaborative=bool(cfg.continued_from_thread_id),
                recent_thread_context=recent_thread_context,
            ),
        }


class DesktopAgentState(FilesystemState, DeepAgentState):
    """Desktop agent state including snapshotted skill files."""


async def _get_agent(config: RunnableConfig) -> Pregel:
    return await build_agent(config)


async def build_agent(config: RunnableConfig, *, tool_surface: ToolSurface | None = None) -> Pregel:
    """Get or create an agent with a sandbox for the given thread."""
    configurable = config.get("configurable") or {}
    cfg = RunConfig.parse(configurable)
    thread_id = cfg.thread_id

    config["recursion_limit"] = DEFAULT_RECURSION_LIMIT

    if thread_id is None or not graph_loaded_for_execution(config):
        logger.info("No thread_id or not for execution, returning agent without sandbox")
        return create_deep_agent(
            system_prompt="",
            tools=[],
        ).with_config(bindable_config(config))

    from agent.incidents.runtime import IncidentMiddleware, IncidentSession, load_incident_session

    incident_session: IncidentSession | None = None
    if cfg.source == "incidents_agent":
        incident_session = await load_incident_session(config)
        cfg.slack_thread = incident_session.slack_thread
        configurable["slack_thread"] = cfg.slack_thread.dump()
    profile_login = await resolve_github_login(as_json_object(config))
    credential_login = None
    credential_scope_known = False
    if not is_desktop_run(cfg):
        try:
            credential_login = await private_credential_login(config)
            credential_scope_known = True
        except Exception:
            logger.exception("Cannot resolve thread credential scope; omitting MCP tools")

    async def reconnect_backend(
        _thread_id: str = thread_id,
        _cfg: RunConfig = cfg,
    ) -> SandboxBackendProtocol:
        if is_desktop_run(_cfg):
            return create_desktop_backend(_cfg)
        return await ensure_sandbox_for_thread(
            _thread_id,
            workspace_slug=workspace_slug(_cfg),
        )

    backend = get_cached_sandbox_backend(thread_id, reconnect=reconnect_backend)
    if tool_surface is None:
        backend.start()

    # `profile_login` is whoever sent the message that started this run; it drives
    # authorization. Personal integrations require verified private ownership.
    # Everything else comes from the thread's own settings, seeded from the first
    # sender's profile and frozen there afterwards.
    local_run = is_desktop_run(cfg)
    # Every settings read below is keyed by this slug. A factory runs outside the
    # graph's own context, so the settings module cannot recover it on its own.
    settings_workspace = workspace_slug(cfg)
    async with aphase(thread_id, "factory.thread_settings"):
        thread_settings, settings_changed = normalize_thread_settings(
            {} if local_run else await load_thread_settings(client, thread_id)
        )
    # Workspace/profile settings are accepted stale for a short TTL so graph factories
    # stay off the critical path during worker load and retry storms.
    settings: WorkspaceSettings | None = None
    if local_run:
        from agent.dashboard.options import default_model_pair

        model_defaults = (default_model_pair(), default_model_pair())
        routing_defaults = {
            "fast": default_model_pair(),
            "balanced": default_model_pair(),
            "performance": default_model_pair(),
        }
        title_defaults = model_defaults[0]
        use_gateway = gateway_env_default()
        profile = None
        fable_enabled = False
    else:
        async with aphase(thread_id, "factory.settings_defaults"):
            settings, profile = await asyncio.gather(
                cached_workspace_settings(settings_workspace),
                _cached_profile(None if thread_settings.get("model_id") else profile_login),
            )
            model_defaults = settings.default_model_pair("agent")
            routing_defaults = settings.agent_routing_models
            title_defaults = settings.default_thread_title_model
            use_gateway = settings.effective_gateway_enabled
            fable_enabled = settings.fable_enabled

    slack_ask_mode = _slack_ask_mode(cfg)
    linear_issue = as_json_object(cfg.linear_issue.model_dump() if cfg.linear_issue else None)
    linear_project_id = linear_issue.get("linear_project_id", "")
    linear_issue_number = linear_issue.get("linear_issue_number", "")

    (model_id, profile_effort), (subagent_model_id, subagent_effort) = model_defaults
    title_model_id, title_effort = title_defaults
    logger.info("Using workspace default agent model: model=%s effort=%s", model_id, profile_effort)

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

    # User preference overrides the workspace's toggle; None inherits it.
    adaptive_model_routing = profile_model_routing_enabled(profile)
    if adaptive_model_routing is None:
        adaptive_model_routing = settings.model_routing_enabled if settings else False
    stored_model = thread_settings.get("model_id")
    if isinstance(stored_model, str):
        model_id = stored_model
        profile_effort = thread_settings.get("effort")
        subagent_model_id = thread_settings.get("subagent_model_id") or stored_model
        subagent_effort = thread_settings.get("subagent_effort")
        adaptive_model_routing = thread_settings.get("model_routing_enabled", False)
        logger.info("Using stored thread settings: model=%s effort=%s", model_id, profile_effort)

    if cfg.source == "dashboard" and cfg.model_selection in {"auto", "explicit"}:
        adaptive_model_routing = cfg.model_selection == "auto"

    # An explicit per-run model choice is the one thing allowed to move a thread
    # off its stored settings; the new choice is then stored in turn.
    per_thread_model = cfg.agent_model_id
    per_thread_effort = cfg.agent_effort
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
    cfg.draft_prs = sender_draft_prs
    if isinstance(thread_settings.get("model_id"), str):
        repo_instructions = thread_settings.get("repo_instructions")
    else:
        async with aphase(thread_id, "factory.repo_instructions"):
            repo_instructions = await _resolve_repo_custom_instructions(
                await _resolve_prompt_default_repo(cfg)
            )
    # Stored before the Fable gate so a deployment-wide toggle still applies on
    # every run rather than being frozen into the thread.
    resolved_settings: ThreadSettings = {
        "model_id": model_id,
        "effort": profile_effort,
        "subagent_model_id": subagent_model_id,
        "subagent_effort": subagent_effort,
        "model_routing_enabled": adaptive_model_routing,
        "repo_instructions": repo_instructions,
    }
    if not local_run and (
        settings_changed or {**thread_settings, **resolved_settings} != thread_settings
    ):
        async with aphase(thread_id, "factory.store_settings"):
            await store_thread_settings(client, thread_id, {**thread_settings, **resolved_settings})

    # A `/oswe` question runs on the asker's own default model, and never routes
    # adaptively: one question gets one answer, so there is nothing to route.
    if slack_ask_mode:
        adaptive_model_routing = False

    model_routing_mode = _model_routing_mode(thread_id) if adaptive_model_routing else None
    config["metadata"] = {
        **(config.get("metadata") or {}),
        "model_routing_applied": adaptive_model_routing,
        **({"model_routing_mode": model_routing_mode} if model_routing_mode else {}),
    }
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

    fallback_model_id = ENV.LLM_FALLBACK_MODEL_ID.optional() or fallback_model_id_for(model_id)
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

    source = cfg.source or "dashboard"
    configurable["source"] = source
    configurable["resolved_agent_model_id"] = model_id
    user_email = cfg.user_email or ""

    async with aphase(thread_id, "factory.admin_thread"):
        admin_thread = await _admin_thread(config, profile_login)
    private_admin_surface = admin_thread and is_private_admin_surface(cfg)
    if admin_thread:
        logger.info("Admin thread %s: adding workspace management tools", thread_id)

    # Channel history pulls messages into the transcript, so everyone who can
    # read the thread reads them. Only a private thread gets the tool at all.
    async with aphase(thread_id, "factory.private_thread"):
        private_thread = await _private_thread(thread_id)

    stop_summary_mode = cfg.stop_summary is True
    sandbox_file_downloads = _sandbox_file_downloads_enabled(cfg)
    mcp_tools: list[Any] = []
    notion_tools: list[Any] = []
    if not stop_summary_mode and not local_run and credential_scope_known:
        mcp_tools, notion_tools = await asyncio.gather(
            _phase_result(
                thread_id,
                "factory.mcp_tools",
                lambda: _mcp_tools_for(
                    credential_login, workspace_slug(cfg) or DEFAULT_WORKSPACE_SLUG
                ),
            ),
            _phase_result(
                thread_id,
                "factory.notion_tools",
                lambda: _notion_tools_for(credential_login),
            ),
        )

    slack_tools = [
        manage_code_channel,
        manage_incident,
        slack_add_reaction,
        slack_attach_html,
        slack_list_channels,
        slack_move_thread,
        slack_no_reply_needed,
        slack_post_message,
        slack_read_thread_messages,
        slack_reply,
        slack_start_new_thread,
    ]
    static_tools = [
        http_request,
        fetch_url,
        web_search,
        background_execute,
        background_task,
        save_plan,
        save_user_instructions,
        *((save_user_settings,) if personal_settings_run_allowed(cfg) else ()),
        save_user_skill,
        delete_user_skill,
        list_threads,
        get_thread,
        manage_thread,
        manage_baby_sit,
        expedite_pr_approval,
        notify_automation_channel,
        open_pull_request,
        *(
            (output_iframe, create_sandbox_file_download_url, expose_port)
            if sandbox_file_downloads
            else ()
        ),
        read_user_settings,
        request_pr_review,
        recreate_sandbox,
        report_platform_issue,
        schedule_thread_wakeup,
        manage_code_channel,
        manage_incident,
        slack_add_reaction,
        slack_attach_html,
        slack_list_channels,
        slack_move_thread,
        slack_no_reply_needed,
        slack_post_message,
        slack_read_channel_messages,
        slack_read_thread_messages,
        slack_reply,
        slack_start_new_thread,
        submit_thread_feedback,
        submit_review_assessment_feedback,
        *(ADMIN_TOOLS if admin_thread else ()),
        *((read_only_sql, manage_review_approval_policy) if private_admin_surface else ()),
    ]
    if credential_login is None:
        personal_tools = (
            save_user_instructions,
            save_user_settings,
            save_user_skill,
            delete_user_skill,
            read_user_settings,
        )
        static_tools = [tool for tool in static_tools if tool not in personal_tools]
    if not private_thread:
        static_tools = [tool for tool in static_tools if tool is not slack_read_channel_messages]
    if not _slack_tools_enabled(cfg):
        static_tools = [tool for tool in static_tools if tool not in slack_tools]
    elif _slack_dm_run(cfg):
        static_tools = [
            tool for tool in static_tools if _registered_tool_name(tool) not in DM_EXCLUDED_TOOLS
        ]
    if (
        local_run
        or not ENV.SLACK_BOT_TOKEN.get()
        or not (await cached_workspace_settings(settings_workspace)).expedited_review_enabled
    ):
        static_tools = [tool for tool in static_tools if tool is not expedite_pr_approval]
    incident_automatic = incident_session is not None and incident_session.explicit_request is None
    if incident_session is not None:
        static_tools.extend(incident_session.tools)
    if incident_automatic:
        static_tools = [
            tool
            for tool in static_tools
            if _registered_tool_name(tool) not in INCIDENT_AUTOMATIC_EXCLUDED_TOOLS
        ]
    static_tools = apply_tool_descriptions(
        static_tools,
        {"expose_port": {"jwks_url": service_identity_jwks_url()}},
    )
    if local_run:
        static_tools = apply_tool_descriptions([http_request, fetch_url, web_search])
    elif stop_summary_mode:
        static_tools = apply_tool_descriptions([slack_read_thread_messages, slack_reply])
    reserved_tool_names = {_registered_tool_name(tool) for tool in static_tools}
    excluded_tools = (
        STOP_SUMMARY_EXCLUDED_TOOLS
        if stop_summary_mode
        else SLACK_ASK_EXCLUDED_TOOLS
        if slack_ask_mode
        else DEEP_AGENT_EXCLUDED_TOOLS | INCIDENT_AUTOMATIC_EXCLUDED_TOOLS
        if incident_automatic
        else DEEP_AGENT_EXCLUDED_TOOLS
    )
    # Nothing is owed on a run the model cannot answer through: an automatic
    # incident sweep, for one, has the reply tool taken away on purpose.
    reply_tool_offered = _registered_tool_name(slack_reply) in reserved_tool_names - excluded_tools
    dynamic_tool_middleware: DynamicToolMiddleware | None = None
    integration_tool_groups: dict[str, IntegrationGroup | Sequence[Any]] = {
        "MCPs": mcp_tools,
        "Notion": notion_tools,
    }
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
    if is_desktop_run(cfg):
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
        if credential_login:
            skill_routes[USER_SKILLS_ROUTE] = ReadOnlyBackend(
                StoreBackend(
                    namespace=lambda _runtime, login=credential_login: (SKILLS_NAMESPACE, login)
                )
            )
            skill_sources.insert(0, USER_SKILLS_ROUTE)
    agent_backend = CompositeBackend(default=backend, routes=skill_routes)
    main_model = _make_model_or_defer(model_id, use_gateway=use_gateway, **model_kwargs)
    model_selection: ModelSelectionMiddleware | None = None
    if adaptive_model_routing:
        assert model_routing_mode is not None
        routing_models = {
            route: _make_model_or_defer(
                routed_model_id,
                use_gateway=use_gateway,
                **provider_model_kwargs(
                    routed_model_id,
                    effort,
                    max_tokens=DEFAULT_LLM_MAX_TOKENS,
                ),
            )
            for route, (routed_model_id, effort) in routing_defaults.items()
        }
        model_selection = ModelSelectionMiddleware(
            routing_models,
            routing_models["fast"],
            route_model_ids={
                route: routed_model_id for route, (routed_model_id, _) in routing_defaults.items()
            },
            routing_mode=model_routing_mode,
        )
    subagent_model = _make_model_or_defer(
        subagent_model_id,
        use_gateway=use_gateway,
        **subagent_model_kwargs,
    )
    title_model = _make_model_or_defer(
        title_model_id,
        use_gateway=use_gateway,
        **title_model_kwargs,
    )
    workspace_skills = (
        WorkspaceSkillsMiddleware(backend=agent_backend, sources=skill_sources)
        if credential_login is None and not local_run
        else None
    )
    async with aphase(thread_id, "factory.graph_assembly"):
        graph = create_deep_agent(
            model=main_model,
            system_prompt="",
            tools=static_tools,
            subagents=[
                _general_purpose_subagent(
                    subagent_model,
                    tools=[tool for tool in static_tools if tool is not save_user_settings],
                    workspace_skills=workspace_skills,
                    dynamic_tools=dynamic_tool_middleware,
                    offloading=ConversationOffloadingMiddleware(subagent_model, agent_backend),
                    incident_middleware=IncidentMiddleware(incident_session)
                    if incident_session is not None
                    else None,
                    guard_middleware=_subagent_guard_middleware(local_run),
                    inherited_middleware_exclusions=(
                        check_message_queue_before_model.name,
                        *((model_selection.name,) if model_selection else ()),
                    ),
                ),
            ],
            skills=skill_sources,
            backend=agent_backend,
            state_schema=DesktopAgentState if local_run else None,
            middleware=cast(
                list[AgentMiddleware[Any, Any, Any]],
                [
                    ConversationOffloadingMiddleware(
                        main_model, agent_backend, manual=cfg.offload_conversation is True
                    ),
                    PrepareAgentRunMiddleware(
                        credential_login=credential_login,
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
                        admin_workspaces=admin_thread,
                        model_selection=model_selection,
                        routing_defaults=routing_defaults,
                    ),
                    TranscriptMiddleware(),
                    *(
                        [IncidentMiddleware(incident_session)]
                        if incident_session is not None
                        else []
                    ),
                    *([workspace_skills] if workspace_skills else []),
                    *([dynamic_tool_middleware] if dynamic_tool_middleware else []),
                    SanitizeToolInputsMiddleware(),
                    ValidateImageReadsMiddleware(),
                    ModelCallLimitMiddleware(
                        run_limit=incident_session.policy.max_model_calls
                        if incident_session is not None
                        else MODEL_CALL_RECURSION_LIMIT,
                        exit_behavior="end",
                    ),
                    ToolErrorMiddleware(),
                    ExcludeToolsMiddleware(excluded=excluded_tools),
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
                    RequireUserReplyMiddleware(
                        _registered_tool_name(slack_reply),
                        _registered_tool_name(slack_no_reply_needed),
                        initial_surface=(
                            _initial_reply_surface(cfg) if reply_tool_offered else WEB_REPLY_SURFACE
                        ),
                    ),
                    notify_step_limit_reached,
                    record_run_usage,
                    *([model_selection] if model_selection else []),
                    *fallback_middleware,
                    SanitizeFireworksMessagesMiddleware(),
                    SanitizeOpenAIResponsesMiddleware(),
                    SanitizeThinkingBlocksMiddleware(),
                    StableToolResultOrderMiddleware(),
                    ModelErrorMiddleware(),
                    # Innermost, so the deadline covers the provider call itself and a
                    # timeout escalates outward to the fallback model.
                    ModelCallTimeoutMiddleware(),
                ],
            ),
        ).with_config(bindable_config(config))
    if tool_surface is not None:
        tool_surface.graph = graph
        tool_surface.dynamic = dynamic_tool_middleware
        tool_surface.excluded = (
            STOP_SUMMARY_EXCLUDED_TOOLS
            if stop_summary_mode
            else SLACK_ASK_EXCLUDED_TOOLS
            if slack_ask_mode
            else DEEP_AGENT_EXCLUDED_TOOLS | INCIDENT_AUTOMATIC_EXCLUDED_TOOLS
            if incident_automatic
            else DEEP_AGENT_EXCLUDED_TOOLS
        )
    elif tools_base_url() and ENV.DASHBOARD_JWT_SECRET.optional() and not local_run:
        await save_tool_context(thread_id, config)
    return graph


async def get_agent(config: RunnableConfig) -> Pregel:
    configurable = (config or {}).get("configurable") or {}
    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str):
        return await _get_agent(config)
    async with aphase(thread_id, "factory.total"):
        return await _get_agent(config)


# langgraph.json entrypoint. Runs trace into LANGSMITH_PROJECT like everything else.
traced_agent = get_agent
