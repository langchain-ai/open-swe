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

from openswe.config import ENV

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
from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemState
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolRetryMiddleware
from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command
from langsmith.sandbox import SandboxRetryableConnectionError


class _DisableInheritedMiddleware(AgentMiddleware):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name


from openswe.analytics.usage import record_agent_invocation_usage
from openswe.bridge.cli_result import cli_result
from openswe.bridge.constants import BridgeClient
from openswe.bridge.store import Bridge
from openswe.bridge.worktree_branch import schedule_worktree_branch_rename
from openswe.bridge.worktree_handoff import worktree_handoff
from openswe.credential_scope import private_credential_login
from openswe.dashboard.agent_overrides import (
    load_profile,
    normalize_profile_overrides,
    normalize_profile_subagent_overrides,
    profile_draft_prs,
    profile_model_routing_enabled,
    resolve_github_login,
)
from openswe.dashboard.options import (
    SUPPORTED_MODEL_IDS,
    ModelOption,
    available_requested_models,
    default_vision_model_pair,
    gate_fable_model,
    model_supports_effort,
    model_supports_images,
)
from openswe.dashboard.workspace_settings import WorkspaceSettings, get_workspace_settings
from openswe.dashboard.workspace_settings_cache import cached_workspace_settings
from openswe.desktop import (
    create_desktop_backend,
    desktop_artifact_routes,
    is_desktop_run,
    is_desktop_worktree,
)
from openswe.github.token import resolve_github_token
from openswe.input_messages import (
    dynamic_context_hash,
    message_sender_id,
    person_introduction,
    visible_dynamic_context_hashes,
)
from openswe.mcp import load_mcp_tools
from openswe.mcp.instance import instance_mcp_source
from openswe.mcp.managed import managed_mcp_source
from openswe.mcp.user import user_mcp_source
from openswe.mcp.workspace import workspace_mcp_source
from openswe.middleware import (
    BasePrepareRunMiddleware,
    DynamicToolMiddleware,
    ExcludeToolsMiddleware,
    ModelCallTimeoutMiddleware,
    ModelErrorMiddleware,
    ModelFallbackMiddleware,
    ModelSelectionMiddleware,
    PullRequestCreationGuardMiddleware,
    RequireUserReplyMiddleware,
    SanitizeFireworksMessagesMiddleware,
    SanitizeOpenAIResponsesMiddleware,
    SanitizeThinkingBlocksMiddleware,
    StableToolResultOrderMiddleware,
    SubdirAgentsReadMiddleware,
    ToolErrorMiddleware,
    ValidateImageReadsMiddleware,
    WorkflowPushGuardMiddleware,
    WorkspaceSkillsMiddleware,
    check_message_queue_before_model,
    deliver_event_matches_before_model,
    notify_step_limit_reached,
    record_run_usage,
    refresh_github_proxy_before_model,
    task_on_failure,
    task_retry_on,
)
from openswe.middleware.client_tools import ClientToolsMiddleware
from openswe.middleware.conversation_offloading import ConversationOffloadingMiddleware
from openswe.middleware.image_model_fallback import ImageModelFallbackMiddleware
from openswe.middleware.model_selection import ModelSelectionState, RoutingMode
from openswe.middleware.prepare_run import PrepareRunState
from openswe.middleware.require_cli_result import RequireCliResultMiddleware
from openswe.middleware.require_user_reply import (
    SLACK_REPLY_SURFACE,
    WEB_REPLY_SURFACE,
    ReplySurface,
)
from openswe.middleware.sandbox_circuit_breaker import post_sandbox_unreachable_notification
from openswe.middleware.stale_workspace import warn_stale_workspace
from openswe.middleware.task_coordination import TaskCoordinationMiddleware
from openswe.middleware.transcript import TranscriptMiddleware
from openswe.model_request import (
    ModelSelectionDecision,
    infer_requested_model,
    model_selection_trace,
)
from openswe.openai_responses.client_tools import CLIENT_OWNED_SERVER_TOOLS
from openswe.prompt import construct_system_prompt
from openswe.prompts import apply_tool_descriptions, prompt
from openswe.review_guide.middleware import ReviewGuideMiddleware
from openswe.review_guide.sessions import ReviewGuideSession
from openswe.run_config import RunConfig
from openswe.runtime.constants import (
    DEFAULT_LLM_MAX_TOKENS,
    DEFAULT_RECURSION_LIMIT,
    MODEL_CALL_RECURSION_LIMIT,
)
from openswe.runtime.constants import (
    DEFAULT_LLM_MODEL_ID as DEFAULT_LLM_MODEL_ID,
)
from openswe.runtime.execution import bindable_config, graph_loaded_for_execution
from openswe.sandboxes.lifecycle import (
    ensure_sandbox_for_thread,
    get_cached_sandbox_backend,
    take_stale_boot,
)
from openswe.sandboxes.paths import resolve_sandbox_work_dir
from openswe.sandboxes.providers.langsmith import service_identity_jwks_url
from openswe.sandboxes.read_only_backend import ReadOnlyBackend
from openswe.sandboxes.retry import SANDBOX_ATTACH_MAX_ELAPSED, retry_transient_sandbox_errors
from openswe.sandboxes.state import (
    SandboxUnreachableError,
    get_or_create_sandbox_backend_proxy,
)
from openswe.sandboxes.tool_access import tools_base_url, tools_endpoint_configured
from openswe.sandboxes.tool_runtime import ToolSurface, save_tool_context
from openswe.skill_store.backend import skills_backend
from openswe.slack.dm import is_concierge_thread, is_dm_channel
from openswe.thread_title import TITLE_GENERATION_MAX_TOKENS, schedule_thread_title_generation
from openswe.threads.blobs import ThreadBlobs, blob_namespace
from openswe.threads.oswe_thread import PREFER_TOOLS_IN_SANDBOX_KEY, OsweThread
from openswe.threads.recent_context import RecentContextAudience, recent_thread_context_section
from openswe.threads.summary import DASHBOARD_SOURCE
from openswe.tools import (
    assign_human_reviewer,
    auto_assign_human_reviewer,
    background_execute,
    background_task,
    code_channel_set_view,
    configure_repository,
    connect_managed_tools,
    create_automation,
    create_sandbox_file_download_url,
    delete_automation,
    delete_organization_skill,
    delete_user_skill,
    delete_workspace,
    dismiss_human_review_request,
    expedite_pr_approval,
    expose_port,
    fetch_url,
    get_human_review_status,
    get_thread,
    http_request,
    link_pull_request,
    list_automations,
    list_event_types,
    list_threads,
    list_workspaces,
    listen_events,
    manage_baby_sit,
    manage_code_channel,
    manage_incident,
    manage_thread,
    merge_expedited_pr,
    open_pull_request,
    output_iframe,
    publish_workspace,
    read_only_sql,
    read_store_item,
    read_user_settings,
    recreate_sandbox,
    refresh_workspace_start,
    report_platform_issue,
    request_human_review,
    request_pr_review,
    request_rollout_check,
    save_organization_skill,
    save_plan,
    save_user_instructions,
    save_user_settings,
    save_user_skill,
    schedule_thread_wakeup,
    search_pull_requests,
    slack_add_reaction,
    slack_attach_html,
    slack_breakout_thread,
    slack_list_channel_members,
    slack_list_channels,
    slack_move_thread,
    slack_no_reply_needed,
    slack_post_message,
    slack_read_channel_messages,
    slack_read_thread_messages,
    slack_reply,
    slack_start_review_channel,
    start_thread,
    submit_thread_feedback,
    suggest_task,
    switch_to_performance_model,
    trigger_automation,
    update_automation,
    web_search,
    write_database_rows,
    write_store_item,
)
from openswe.tools.access import permitted, resolve_access
from openswe.tools.admin_gate import (
    actor_has_admin_context,
    participant_is_admin,
)
from openswe.tools.manage_feature_flags import manage_feature_flags
from openswe.tools.manage_review_approval_mode import manage_review_approval_mode
from openswe.tools.propose_pr_review import propose_pr_review
from openswe.tools.propose_review_comment import propose_review_comment
from openswe.tools.review_walkthrough import walkthrough_tools
from openswe.tools.sandbox_preference import CURL_REPLACED_TOOLS, SANDBOX_ONLY_TOOLS
from openswe.tools.submit_review_assessment_feedback import submit_review_assessment_feedback
from openswe.tools.task_threads import (
    control_worker,
    message_task_thread,
    spawn_worker,
    task_status,
)
from openswe.users import User
from openswe.utils import ttl_cache
from openswe.utils.authorship import (
    OPEN_SWE_BOT_EMAIL,
    OPEN_SWE_BOT_NAME,
    CollaboratorIdentity,
    ThreadParticipant,
    resolve_participant_identities,
    resolve_triggering_user_identity,
)
from openswe.utils.dashboard_links import dashboard_base_url, dashboard_plan_url
from openswe.utils.deferred_model import make_deferred_error_model
from openswe.utils.gateway import gateway_env_default
from openswe.utils.json_types import as_json_object, thread_metadata
from openswe.utils.model import (
    DEFAULT_LLM_REASONING,
    ModelKwargs,
    fallback_model_id_for,
    make_model,
    provider_model_kwargs,
)
from openswe.utils.startup_trace import aphase
from openswe.utils.thread_participants import PARTICIPANT_LOGINS_KEY, participant_logins
from openswe.utils.thread_settings import (
    ThreadSettings,
    load_thread_settings,
    normalize_thread_settings,
    store_thread_settings,
)
from openswe.workspaces.store import (
    DEFAULT_WORKSPACE_SLUG,
    load_workspace,
)

client = get_client()

USER_SKILLS_ROUTE = "/skills/"
ORGANIZATION_SKILLS_ROUTE = "/organization-skills/"
BUNDLED_SKILLS_ROUTE = "/bundled-skills/"
BLOBS_ROUTE = "/blobs/"
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
# A review walkthrough's prepare run reads the diff and queues chunks; it changes nothing.
GUIDE_PREFETCH_EXCLUDED_TOOLS = frozenset({"delete", "edit_file", "task", "write_file"})
# Each posts to the reader and ends the walkthrough's turn as surely as a final reply.
GUIDE_REPLY_TOOLS = frozenset({"show_chunk", "show_queued", "show_other", "end_walkthrough"})
# A `/oswe` request has a channel but no Slack thread, so only the tools that act
# on one are out of reach. Everything else, writes included, stays available.
SLACK_ASK_EXCLUDED_TOOLS = DEEP_AGENT_EXCLUDED_TOOLS | frozenset(
    {
        "code_channel_set_view",
        "manage_code_channel",
        "manage_incident",
        "slack_add_reaction",
        "slack_attach_html",
        "slack_move_thread",
        "slack_start_review_channel",
    }
)
SLACK_BY_THE_WAY_EXCLUDED_TOOLS = SLACK_ASK_EXCLUDED_TOOLS | frozenset({"slack_breakout_thread"})


def _slack_ask_excluded_tools(cfg: RunConfig) -> frozenset[str]:
    if cfg.slack_by_the_way_thread_ts:
        return SLACK_BY_THE_WAY_EXCLUDED_TOOLS
    return SLACK_ASK_EXCLUDED_TOOLS


# Reading a Slack channel takes an explicit channel id and nothing from the run's
# source context, so it survives every gate the thread-bound Slack tools do not.
SOURCE_FREE_SLACK_TOOLS: frozenset[str] = frozenset({"slack_read_channel_messages"})


def _registered_tool_name(value: Any) -> str:
    name = getattr(value, "name", None) or getattr(value, "__name__", None)
    if not isinstance(name, str) or not name:
        raise TypeError(f"tool has no registered name: {value!r}")
    return name


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
        from openswe.dashboard.agent_instructions import get_repo_agent_instructions

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
    slack_user_id: str = "",
) -> ThreadParticipant:
    login = identity.github_login or None
    if login is None:
        return ThreadParticipant(
            identity=identity,
            person_id=person_id or "",
            timezone=timezone,
            slack_user_id=slack_user_id,
        )
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
        slack_user_id=slack_user_id or (user.slack_user_id if user else ""),
    )


async def _thread_participants(
    thread_id: str,
    config: RunnableConfig,
    sender: CollaboratorIdentity | None,
    *,
    sender_person_id: str,
    sender_display_name: str = "",
    sender_timezone: str = "",
    sender_slack_user_id: str = "",
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
                resolved_sender,
                config,
                person_id=sender_person_id,
                timezone=sender_timezone,
                slack_user_id=sender_slack_user_id,
            ),
            *(_thread_participant(identity, config) for identity in others),
        )
    )


async def _resolve_user_custom_instructions(login: str | None) -> str | None:
    """Load user-level custom agent instructions for the triggering user."""
    if not login:
        return None
    try:
        from openswe.dashboard.user_instructions import get_user_custom_instructions

        return await get_user_custom_instructions(login)
    except Exception:
        logger.debug("Failed to load user custom agent instructions", exc_info=True)
        return None


INCIDENT_AUTOMATIC_EXCLUDED_TOOLS: frozenset[str] = frozenset(
    {
        "code_channel_set_view",
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
        "merge_expedited_pr",
        "request_human_review",
        "assign_human_reviewer",
        "auto_assign_human_reviewer",
        "dismiss_human_review_request",
        "get_human_review_status",
        "manage_baby_sit",
        "listen_events",
        "request_rollout_check",
        "manage_thread",
        "link_pull_request",
        "open_pull_request",
        "recreate_sandbox",
        "request_pr_review",
        "save_user_skill",
        "delete_user_skill",
        "slack_move_thread",
        "slack_post_message",
        "slack_breakout_thread",
        "slack_start_review_channel",
        "publish_workspace",
        "refresh_workspace_start",
        "configure_repository",
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
        "code_channel_set_view",
        "manage_code_channel",
        "manage_incident",
        "list_threads",
        "listen_events",
        "request_rollout_check",
        "manage_thread",
        "read_incident",
        "read_only_sql",
        "read_user_settings",
        "connect_managed_tools",
        "save_user_settings",
        "record_incident_report",
        "search_incidents",
        "start_thread",
    }


class _SubagentToolGuard(AgentMiddleware):
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if _is_subagent_excluded_tool(request.tool_call["name"]):
            return ToolMessage(
                content=prompt("tools/subagent-unavailable"),
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
            f"{prompt('system/general-purpose-subagent-suffix')}"
        ),
        "mode": "fork",
        "model": model,
        "tools": list(tools),
        "middleware": cast(
            list[AgentMiddleware[Any, Any, Any]],
            [
                _DisableInheritedMiddleware(RequireUserReplyMiddleware.__name__),
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


# Workspace-admin tools; each declares where it may run with `@access`.
ADMIN_TOOLS = (
    list_automations,
    create_automation,
    update_automation,
    trigger_automation,
    delete_automation,
    list_workspaces,
    publish_workspace,
    refresh_workspace_start,
    configure_repository,
    delete_workspace,
    save_organization_skill,
    delete_organization_skill,
)


def workspace_slug(cfg: RunConfig) -> str | None:
    """The workspace this thread selected, if any."""
    return cfg.workspace_slug


async def _admin_thread(config: RunnableConfig, profile_login: str | None) -> bool:
    """Whether this run may manage workspaces and organization skills."""
    return await actor_has_admin_context(RunConfig.from_config(config), login=profile_login)


async def _bridge_client(thread_id: str | None) -> BridgeClient | None:
    """The app serving this thread's bridge to the user's machine, if it has one."""
    if not thread_id:
        return None
    try:
        thread = await client.threads.get(thread_id=thread_id)
    except Exception:
        logger.warning(
            "Could not read the thread's sandbox; treating it as hosted",
            extra={"agent_thread_id": thread_id},
            exc_info=True,
        )
        return None
    metadata = thread_metadata(thread)
    sandbox_id = metadata.get("sandbox_id")
    if not isinstance(sandbox_id, str) or Bridge.bridge_id_of(sandbox_id) is None:
        return None
    # Bound before bridges recorded their client, which only the CLI served then.
    return "desktop" if metadata.get("sandbox_bridge_client") == "desktop" else "cli"


async def _mcp_tools_for(
    credential_login: str | None, workspace: str, managed_gateway: str | None
) -> list[Any]:
    """Load the run's MCPs by tier: instance, then workspace, then the user's own.

    A later tier's connection replaces a same-named one from the tier before. The
    workspace's LangSmith Managed Tools gateway comes last, used as the private owner.
    """
    sources = [instance_mcp_source(), workspace_mcp_source(workspace)]
    if credential_login:
        sources.append(user_mcp_source(credential_login))
        if managed_gateway:
            sources.append(managed_mcp_source(credential_login, managed_gateway))
    return await load_mcp_tools(*sources)


async def _cached_profile(profile_login: str | None):
    if not profile_login:
        return None
    return await ttl_cache.cached(
        f"profile:{profile_login}", 30, lambda: load_profile(profile_login)
    )


def _sandbox_file_downloads_enabled(cfg: RunConfig, *, bridged: bool) -> bool:
    """Return whether signed sandbox file downloads are available for this run.

    They are served by the LangSmith box itself, which a bridged thread does not have.
    """
    return (
        ENV.SANDBOX_TYPE.get() == "langsmith"
        and cfg.stop_summary is not True
        and not bridged
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
    """A one-off `/oswe` question (ephemeral answer, no Slack thread), until it continues on the web."""
    return (
        cfg.source == "slack"
        and cfg.slack_ask is True
        and cfg.slack_thread is not None
        and bool(cfg.slack_thread.triggering_user_id.strip())
    )


def _slack_concierge_run(cfg: RunConfig) -> bool:
    """Whether this run answers in a bot DM its owner runs in concierge mode."""
    return (
        _slack_tools_enabled(cfg)
        and cfg.slack_thread is not None
        and is_concierge_thread(cfg.slack_thread.channel_context, cfg.slack_thread.thread_ts)
    )


def _model_routing_mode(thread_id: str) -> RoutingMode:
    digest = hashlib.sha256(thread_id.encode()).hexdigest()
    bucket = int(digest[:8], 16) / float(0xFFFF_FFFF)
    return "auto" if bucket < _MODEL_ROUTING_SPLIT else "fast"


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
        recent_thread_context_enabled: bool,
        admin_workspaces: bool,
        sole_writer: bool = False,
        model_selection: ModelSelectionMiddleware | None = None,
        routing_defaults: Mapping[str, tuple[str, str | None]] | None = None,
        credential_login: str | None = None,
        requested_models: Mapping[str, ModelOption] | None = None,
        saved_requested_model: str | None = None,
        bridge_client: BridgeClient | None = None,
        prefer_tools_in_sandbox: bool = False,
    ) -> None:
        self._bridge_client = bridge_client
        self._saved_requested_model = saved_requested_model
        self._prefer_tools_in_sandbox = prefer_tools_in_sandbox
        self._requested_models = requested_models
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
        self._recent_thread_context_enabled = recent_thread_context_enabled
        self._admin_workspaces = admin_workspaces
        self._sole_writer = sole_writer
        self._model_selection = model_selection
        self._routing_defaults = dict(routing_defaults or {})

    def _recent_context_audience(self, cfg: RunConfig) -> RecentContextAudience | None:
        if (
            not self._recent_thread_context_enabled
            or cfg.background_task_completion
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
            "recent_thread_context_enabled": self._recent_thread_context_enabled,
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

    async def _prepare(self, state: PrepareRunState, runtime: Runtime) -> dict[str, object]:
        decision = ModelSelectionDecision(requested_model=self._saved_requested_model)
        cfg = RunConfig.from_config(self._config)
        if cfg.model_selection == "explicit":
            decision.reason = "explicit_selection"
        elif (
            cfg.source == "dashboard"
            and cfg.model_selection == "auto"
            and cfg.model_selection_changed
        ):
            decision.reason = "deliberate_auto_reset"
        elif self._saved_requested_model:
            decision.outcome = "reused_saved_choice"
            decision.reason = "saved_opening_request"
            decision.pin_persisted = True
        async with model_selection_trace() as span:
            prepared: dict[str, object] = {}
            try:
                prepared = await self._prepare_with_decision(state, runtime, decision)
                return prepared
            finally:
                span.end(
                    outputs={
                        "requested_model": decision.requested_model,
                        "classifier": vars(decision.classifier),
                        "requested_effort": decision.requested_effort,
                        "effort_classifier": vars(decision.effort_classifier),
                        "outcome": decision.outcome,
                        "reason": decision.reason,
                        "pin_persisted": decision.pin_persisted,
                        "selected_model_id": prepared.get("selected_model_id"),
                        "selected_effort": prepared.get("selected_effort"),
                        "route": prepared.get("model_route"),
                    },
                    error=None if prepared else "Model selection preparation failed",
                )

    async def _prepare_with_decision(
        self, state: PrepareRunState, runtime: Runtime, decision: ModelSelectionDecision
    ) -> dict[str, object]:  # noqa: ARG002
        schedule_thread_title_generation(
            thread_id=self._thread_id,
            messages=state.get("messages") or [],
            model=self._title_model,
            client=client,
        )
        requested_model: str | None = None
        requested_effort: str | None = None
        if self._requested_models is not None and self._model_selection is not None:
            settings = (await load_thread_settings(client, self._thread_id)).copy()
            if not settings.get("model_handoff_complete"):
                handoff_config = RunConfig.from_config(self._config)
                handoff = await infer_requested_model(
                    messages=state.get("messages") or [],
                    requested_models=self._requested_models,
                    decision=decision.classifier,
                    effort_decision=decision.effort_classifier,
                    slack_event_ts=(
                        handoff_config.slack_thread.triggering_event_ts
                        if handoff_config.slack_thread is not None
                        else None
                    ),
                )
                decision.outcome = "classifier_failure" if handoff is None else "no_request"
                decision.reason = (
                    "no_model_request" if handoff is not None else decision.classifier.reason
                )
                if decision.classifier.outcome == "low_confidence":
                    decision.outcome = "low_confidence"
                if handoff is not None:
                    requested_model = handoff.requested_model
                    requested_effort = handoff.requested_effort
                    decision.requested_effort = requested_effort
                    decision.requested_model = requested_model
                    if handoff.unavailable_model or (
                        requested_model and requested_model not in self._requested_models
                    ):
                        decision.outcome = "unavailable_request"
                        decision.reason = "model_unavailable"
                        raise ValueError(
                            "The requested runtime model is unavailable; select an available model."
                        )
                if handoff is not None and handoff.unavailable_effort:
                    decision.outcome = "unavailable_request"
                    decision.reason = "effort_unavailable"
                    raise ValueError("The requested reasoning effort is unavailable.")
                if requested_effort is not None:
                    requested_model = requested_model or self._model_id
                    decision.requested_model = requested_model
                    option = self._requested_models.get(requested_model)
                    if option is None:
                        decision.outcome = "unavailable_request"
                        decision.reason = "model_unavailable"
                        raise ValueError(
                            "Choose an available runtime model to set its reasoning effort."
                        )
                    if requested_effort not in option["efforts"]:
                        decision.outcome = "incompatible_request"
                        decision.reason = "effort_unsupported"
                        raise ValueError(
                            f"The requested reasoning effort {requested_effort!r} is not supported "
                            f"by {option['label']}; choose from {', '.join(option['efforts'])}."
                        )
                settings["model_handoff_complete"] = True
                settings["requested_model"] = requested_model
                if requested_model:
                    option = self._requested_models[requested_model]
                    if not option["supports_images"] and any(
                        block.get("type") == "image"
                        for message in state.get("messages", [])
                        if isinstance(message, HumanMessage)
                        for block in message.content_blocks
                    ):
                        decision.outcome = "incompatible_request"
                        decision.reason = "image_input_unsupported"
                        raise ValueError(
                            "The requested runtime model does not support image input; "
                            "select an image-capable model."
                        )
                    settings.update(
                        model_id=requested_model,
                        effort=requested_effort or option["default_effort"],
                        model_routing_enabled=False,
                    )
                try:
                    await store_thread_settings(client, self._thread_id, settings, strict=True)
                except Exception:
                    decision.outcome = "persistence_failure"
                    decision.reason = "settings_write_failed"
                    raise
                decision.pin_persisted = bool(requested_model)
            else:
                requested_model = settings.get("requested_model")
                decision.requested_model = requested_model
                decision.outcome = "reused_saved_choice" if requested_model else "not_classified"
                decision.reason = (
                    "saved_opening_request" if requested_model else "handoff_already_complete"
                )
                decision.pin_persisted = bool(requested_model)
            if requested_model:
                option = self._requested_models[requested_model]
                requested_effort = settings.get("effort") or option["default_effort"]
                decision.requested_effort = requested_effort
                try:
                    self._model_selection.use_requested_model(requested_model, requested_effort)
                except Exception:
                    decision.outcome = "selection_failure"
                    decision.reason = "model_initialization_failed"
                    raise
                if decision.outcome != "reused_saved_choice":
                    decision.outcome = "accepted_request"
                    decision.reason = "validated_and_persisted"
                self._model_id = requested_model
                self._effort = requested_effort
        configurable = (self._config or {}).get("configurable") or {}
        configurable["draft_prs"] = self._draft_prs
        cfg = RunConfig.parse(configurable)
        if is_desktop_run(cfg):
            async with aphase(self._thread_id, "prepare.await_sandbox"):
                try:
                    sandbox_proxy = get_or_create_sandbox_backend_proxy(self._thread_id)
                    sandbox_backend = await retry_transient_sandbox_errors(
                        sandbox_proxy.ready,
                        description="Sandbox attach",
                        max_elapsed=SANDBOX_ATTACH_MAX_ELAPSED,
                    )
                except (SandboxUnreachableError, SandboxRetryableConnectionError) as exc:
                    await post_sandbox_unreachable_notification(
                        self._config or {},
                        sandbox_id=exc.sandbox_id
                        if isinstance(exc, SandboxUnreachableError)
                        else None,
                    )
                    raise
            if cfg.local_project_path and is_desktop_worktree(cfg.local_project_path):
                schedule_worktree_branch_rename(
                    thread_id=self._thread_id,
                    backend=sandbox_backend,
                    messages=state.get("messages") or [],
                    model=self._title_model,
                )
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
        sandbox_proxy = get_or_create_sandbox_backend_proxy(self._thread_id)
        sandbox_task = asyncio.create_task(
            retry_transient_sandbox_errors(
                sandbox_proxy.ready,
                description="Sandbox attach",
                max_elapsed=SANDBOX_ATTACH_MAX_ELAPSED,
            )
        )
        try:
            async with aphase(self._thread_id, "prepare.await_sandbox"):
                triggering_user_identity, sandbox_backend = await asyncio.gather(
                    triggering_user_identity_task,
                    sandbox_task,
                )
        except (SandboxUnreachableError, SandboxRetryableConnectionError) as exc:
            # The run is about to die with no sandbox; make sure the user hears
            # why rather than getting silence.
            await post_sandbox_unreachable_notification(
                self._config or {},
                sandbox_id=exc.sandbox_id if isinstance(exc, SandboxUnreachableError) else None,
            )
            raise
        del github_token
        if stale_workspace := take_stale_boot(self._thread_id):
            await warn_stale_workspace(self._config or {}, self._thread_id, stale_workspace)
        async with aphase(self._thread_id, "prepare.work_dir"):
            work_dir = await resolve_sandbox_work_dir(sandbox_backend)
        bridged = Bridge.bridge_id_of(sandbox_backend.id) is not None
        if bridged and self._bridge_client == "desktop":
            schedule_worktree_branch_rename(
                thread_id=self._thread_id,
                backend=sandbox_backend,
                messages=state.get("messages") or [],
                model=self._title_model,
            )
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
                routing_state = cast(ModelSelectionState, state).copy()
                if (
                    cfg.source == "dashboard"
                    and cfg.model_selection == "auto"
                    and cfg.model_selection_changed
                ):
                    routing_state.pop("model_route", None)
                    routing_state.pop("requested_model", None)
                attribution_route = (
                    "default"
                    if requested_model
                    else await self._model_selection.select_route(routing_state)
                )
                if attribution_route != "default":
                    attribution_model_id, attribution_effort = self._routing_defaults[
                        attribution_route
                    ]
            configurable["resolved_agent_model_id"] = attribution_model_id
            configurable["resolved_agent_effort"] = attribution_effort
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
                    sender_slack_user_id=(
                        cfg.slack_thread.triggering_user_id if cfg.slack_thread else ""
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
                        **(
                            {"model_route": attribution_route}
                            if attribution_route is not None
                            else {}
                        ),
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
            "requested_model": requested_model,
            "requested_effort": requested_effort,
            "selected_model_id": attribution_model_id,
            "selected_effort": attribution_effort,
            **({"messages": sender_messages} if sender_messages else {}),
            **({"model_route": attribution_route} if attribution_route is not None else {}),
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
                workspace_repos=workspace.repos if workspace else None,
                admin_workspaces=self._admin_workspaces,
                sole_writer=self._sole_writer,
                source="background_task" if cfg.background_task_completion else self._source,
                slack_context=_slack_tools_enabled(cfg),
                slack_ask=_slack_ask_mode(cfg),
                slack_by_the_way=_slack_ask_mode(cfg) and bool(cfg.slack_by_the_way_thread_ts),
                slack_breakout=cfg.slack_breakout is True,
                slack_follow_up_suggestions=_slack_concierge_run(cfg)
                or (await cached_workspace_settings(workspace_slug(cfg))).get(
                    "slack_follow_up_suggestions"
                )
                is True,
                sandbox_file_downloads=_sandbox_file_downloads_enabled(cfg, bridged=bridged),
                prefer_tools_in_sandbox=self._prefer_tools_in_sandbox,
                continued_from_collaborative=bool(cfg.continued_from_thread_id),
                local_checkout=bridged,
                local_checkout_client=self._bridge_client or "cli",
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

    from openswe.incidents.runtime import IncidentMiddleware, IncidentSession, load_incident_session

    incident_session: IncidentSession | None = None
    if cfg.source == "incidents_agent":
        incident_session = await load_incident_session(config)
        cfg.slack_thread = incident_session.slack_thread
        configurable["slack_thread"] = cfg.slack_thread.dump()
    guide = await ReviewGuideSession.get(thread_id)
    if guide is not None and guide.closed:
        guide = None
    guide_prefetch = guide is not None and cfg.review_guide_prefetch
    profile_login = await resolve_github_login(as_json_object(config))
    credential_login = None
    credential_scope_known = False
    if not is_desktop_run(cfg):
        try:
            credential_login = await private_credential_login(config)
            credential_scope_known = True
        except Exception:
            logger.exception("Cannot resolve thread credential scope; omitting MCP tools")

    local_run = is_desktop_run(cfg)
    task_coordination = (
        None if local_run else await TaskCoordinationMiddleware.for_thread(thread_id)
    )

    async def reconnect_backend(
        _thread_id: str = thread_id,
        _cfg: RunConfig = cfg,
    ) -> SandboxBackendProtocol:
        if is_desktop_run(_cfg):
            return create_desktop_backend(_cfg)
        return await ensure_sandbox_for_thread(
            _thread_id,
            workspace_slug=workspace_slug(_cfg),
            record_stale_boot=True,
        )

    backend = get_cached_sandbox_backend(thread_id, reconnect=reconnect_backend)
    if tool_surface is None:
        backend.start()

    # `profile_login` is whoever sent the message that started this run; it drives
    # authorization. Personal integrations require verified private ownership.
    # Everything else comes from the thread's own settings, seeded from the first
    # sender's profile and frozen there afterwards.
    reset_model_selection = (
        cfg.source == "dashboard" and cfg.model_selection == "auto" and cfg.model_selection_changed
    )
    # Every settings read below is keyed by this slug. A factory runs outside the
    # graph's own context, so the settings module cannot recover it on its own.
    settings_workspace = workspace_slug(cfg)
    async with aphase(thread_id, "factory.bridged_thread"):
        bridge_client = await _bridge_client(thread_id)
    async with aphase(thread_id, "factory.thread_settings"):
        thread_settings, settings_changed = normalize_thread_settings(
            {} if local_run else await load_thread_settings(client, thread_id)
        )
        # Bridged threads and deployments without a tools endpoint have no way to
        # reach sandbox-only tools, so they keep every tool direct.
        prefer_tools_in_sandbox = (
            not local_run
            and bridge_client is None
            and tools_endpoint_configured()
            and await OsweThread.prefers_tools_in_sandbox(client, thread_id)
        )
    # Workspace/profile settings are accepted stale for a short TTL so graph factories
    # stay off the critical path during worker load and retry storms.
    settings: WorkspaceSettings | None = None
    routing_defaults: dict[str, tuple[str, str | None]]
    if local_run:
        from openswe.dashboard.options import default_model_pair

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
                _cached_profile(
                    profile_login
                    if reset_model_selection or not thread_settings.get("model_id")
                    else None
                ),
            )
            model_defaults = settings.default_model_pair("agent")
            routing_defaults = dict(settings.agent_routing_models)
            title_defaults = settings.default_thread_title_model
            use_gateway = settings.effective_gateway_enabled
            fable_enabled = settings.fable_enabled

    slack_ask_mode = _slack_ask_mode(cfg)
    linear_issue = as_json_object(cfg.linear_issue.model_dump() if cfg.linear_issue else None)
    linear_project_id = linear_issue.get("linear_project_id", "")
    linear_issue_number = linear_issue.get("linear_issue_number", "")

    (model_id, profile_effort), (subagent_model_id, subagent_effort) = model_defaults
    for route, stored_route in thread_settings.get("routing_models", {}).items():
        if route in routing_defaults:
            routing_defaults[route] = (stored_route["model_id"], stored_route["effort"])
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
    if isinstance(stored_model, str) and not reset_model_selection:
        model_id = stored_model
        profile_effort = thread_settings.get("effort")
        subagent_model_id = thread_settings.get("subagent_model_id") or stored_model
        subagent_effort = thread_settings.get("subagent_effort")
        adaptive_model_routing = thread_settings.get("model_routing_enabled", False)
        logger.info("Using stored thread settings: model=%s effort=%s", model_id, profile_effort)

    if cfg.model_selection == "explicit":
        adaptive_model_routing = False
        thread_settings["requested_model"] = None
        thread_settings["model_handoff_complete"] = True
        settings_changed = True
    elif cfg.source == "dashboard" and cfg.model_selection == "auto":
        if reset_model_selection:
            thread_settings["requested_model"] = None
            thread_settings["model_handoff_complete"] = True
            settings_changed = True
        adaptive_model_routing = not bool(thread_settings.get("requested_model"))

    if cfg.source == "incidents_agent":
        adaptive_model_routing = False
        if not cfg.agent_model_id:
            model_id, profile_effort = routing_defaults["performance"]
            subagent_model_id, subagent_effort = routing_defaults["performance"]

    # Auto never falls back outside its tiers: an uncertain route uses Fast.
    if adaptive_model_routing and not slack_ask_mode:
        if (subagent_model_id, subagent_effort) == (model_id, profile_effort):
            subagent_model_id, subagent_effort = routing_defaults["fast"]
        model_id, profile_effort = routing_defaults["fast"]

    # Capability fallbacks can temporarily replace a pinned text-only model.
    image_model_override: tuple[str, str] | None = None
    per_thread_model = cfg.agent_model_id
    per_thread_effort = cfg.agent_effort
    if (
        (
            not thread_settings.get("requested_model")
            or cfg.model_selection == "explicit"
            or (cfg.model_override_reason == "image_input" and not model_supports_images(model_id))
        )
        and isinstance(per_thread_model, str)
        and (not reset_model_selection or cfg.model_override_reason == "image_input")
        and per_thread_model in SUPPORTED_MODEL_IDS
        and isinstance(per_thread_effort, str)
        and model_supports_effort(per_thread_model, per_thread_effort)
    ):
        logger.info(
            "Applying per-thread model override",
            extra={"model_id": per_thread_model, "effort": per_thread_effort},
        )
        if (
            thread_settings.get("requested_model") or reset_model_selection
        ) and cfg.model_override_reason == "image_input":
            image_model_override = (per_thread_model, per_thread_effort)
        else:
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
        "model_handoff_complete": thread_settings.get("model_handoff_complete", bool(stored_model)),
        "routing_models": {
            route: {"model_id": routed_model_id, "effort": effort}
            for route, (routed_model_id, effort) in routing_defaults.items()
        },
        "repo_instructions": repo_instructions,
    }
    if not local_run and (
        settings_changed or {**thread_settings, **resolved_settings} != thread_settings
    ):
        async with aphase(thread_id, "factory.store_settings"):
            await store_thread_settings(client, thread_id, {**thread_settings, **resolved_settings})

    if image_model_override is not None:
        model_id, profile_effort = image_model_override
        subagent_model_id, subagent_effort = image_model_override

    # A `/oswe` question runs on the asker's own default model, and never routes
    # adaptively: one question gets one answer, so there is nothing to route.
    if slack_ask_mode:
        adaptive_model_routing = False

    model_routing_mode = _model_routing_mode(thread_id) if adaptive_model_routing else None
    config["metadata"] = {
        **(config.get("metadata") or {}),
        "model_routing_applied": adaptive_model_routing,
        **({"model_routing_mode": model_routing_mode} if model_routing_mode else {}),
        PREFER_TOOLS_IN_SANDBOX_KEY: prefer_tools_in_sandbox,
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

    def make_fallback_model(primary_model_id: str) -> BaseChatModel | None:
        fallback_model_id = ENV.LLM_FALLBACK_MODEL_ID.optional() or fallback_model_id_for(
            primary_model_id
        )
        if not fallback_model_id or fallback_model_id == primary_model_id:
            return None
        fallback_kwargs: ModelKwargs = {"max_tokens": DEFAULT_LLM_MAX_TOKENS}
        if fallback_model_id.startswith("openai:"):
            fallback_kwargs["reasoning"] = DEFAULT_LLM_REASONING
        logger.info(
            "Configured model fallback",
            extra={"primary_model_id": primary_model_id, "fallback_model_id": fallback_model_id},
        )
        return _make_model_or_defer(fallback_model_id, use_gateway=use_gateway, **fallback_kwargs)

    fallback_middleware = ModelFallbackMiddleware(make_fallback_model(model_id))

    source = cfg.source or "dashboard"
    configurable["source"] = source
    configurable["resolved_agent_model_id"] = model_id
    configurable["resolved_agent_effort"] = profile_effort
    user_email = cfg.user_email or ""

    async with aphase(thread_id, "factory.admin_thread"):
        admin_thread = await _admin_thread(config, profile_login)
    async with aphase(thread_id, "factory.tool_access"):
        tool_access = await resolve_access(cfg, login=profile_login)

    stop_summary_mode = cfg.stop_summary is True
    cli_result_required = not stop_summary_mode and bridge_client == "cli"
    sandbox_file_downloads = _sandbox_file_downloads_enabled(cfg, bridged=bridge_client is not None)
    mcp_tools: list[Any] = []
    workspace = workspace_slug(cfg) or DEFAULT_WORKSPACE_SLUG
    managed_gateway = (
        (await cached_workspace_settings(workspace)).managed_tools_gateway_id
        if credential_login and not stop_summary_mode and not local_run
        else None
    )
    if not stop_summary_mode and not local_run and credential_scope_known:
        async with aphase(thread_id, "factory.mcp_tools"):
            mcp_tools = await _mcp_tools_for(credential_login, workspace, managed_gateway)

    slack_tools = [
        manage_code_channel,
        code_channel_set_view,
        manage_incident,
        slack_add_reaction,
        slack_attach_html,
        slack_list_channel_members,
        slack_list_channels,
        slack_move_thread,
        slack_no_reply_needed,
        slack_post_message,
        slack_read_thread_messages,
        slack_reply,
        slack_breakout_thread,
        slack_start_review_channel,
    ]
    static_tools = [
        http_request,
        fetch_url,
        web_search,
        background_execute,
        background_task,
        save_plan,
        save_user_instructions,
        save_user_settings,
        save_user_skill,
        delete_user_skill,
        list_threads,
        search_pull_requests,
        get_thread,
        manage_thread,
        *(
            [spawn_worker]
            if task_coordination and task_coordination.enabled and not task_coordination.is_worker
            else []
        ),
        *([task_status, message_task_thread, control_worker] if task_coordination else []),
        *((start_thread,) if _slack_concierge_run(cfg) else ()),
        manage_baby_sit,
        switch_to_performance_model,
        expedite_pr_approval,
        merge_expedited_pr,
        request_human_review,
        assign_human_reviewer,
        auto_assign_human_reviewer,
        dismiss_human_review_request,
        get_human_review_status,
        open_pull_request,
        link_pull_request,
        *(
            (output_iframe, create_sandbox_file_download_url, expose_port)
            if sandbox_file_downloads
            else ()
        ),
        read_user_settings,
        request_pr_review,
        *((connect_managed_tools,) if managed_gateway else ()),
        recreate_sandbox,
        report_platform_issue,
        schedule_thread_wakeup,
        request_rollout_check,
        listen_events,
        list_event_types,
        manage_code_channel,
        code_channel_set_view,
        manage_incident,
        slack_add_reaction,
        slack_attach_html,
        slack_list_channel_members,
        slack_list_channels,
        slack_move_thread,
        slack_no_reply_needed,
        slack_post_message,
        slack_read_channel_messages,
        slack_read_thread_messages,
        slack_reply,
        slack_breakout_thread,
        slack_start_review_channel,
        submit_thread_feedback,
        suggest_task,
        submit_review_assessment_feedback,
        propose_review_comment,
        propose_pr_review,
        *ADMIN_TOOLS,
        *((cli_result,) if cli_result_required else ()),
        *((worktree_handoff,) if bridge_client == "desktop" and not stop_summary_mode else ()),
        read_only_sql,
        read_store_item,
        *(
            (write_store_item, write_database_rows)
            if ENV.OPENSWE_ENV.optional() in {"preview", "staging"}
            else ()
        ),
        manage_feature_flags,
        manage_review_approval_mode,
    ]
    static_tools = permitted(static_tools, tool_access)
    if not _slack_tools_enabled(cfg):
        # An automation run has no Slack thread, but its prompt may ask it to
        # report to a channel.
        kept = (slack_list_channels, slack_post_message) if cfg.source == "schedule" else ()
        static_tools = [tool for tool in static_tools if tool not in slack_tools or tool in kept]
    elif _slack_concierge_run(cfg):
        static_tools = [
            tool for tool in static_tools if _registered_tool_name(tool) not in DM_EXCLUDED_TOOLS
        ]
    if local_run or not ENV.SLACK_BOT_TOKEN.get():
        static_tools = [
            tool
            for tool in static_tools
            if tool
            not in (
                request_human_review,
                assign_human_reviewer,
                auto_assign_human_reviewer,
                dismiss_human_review_request,
                get_human_review_status,
            )
        ]
    if (
        local_run
        or not ENV.SLACK_BOT_TOKEN.get()
        or not (await cached_workspace_settings(settings_workspace)).expedited_review_enabled
    ):
        static_tools = [
            tool for tool in static_tools if tool not in (expedite_pr_approval, merge_expedited_pr)
        ]
    incident_automatic = incident_session is not None and incident_session.explicit_request is None
    if incident_session is not None:
        static_tools.extend(incident_session.tools)
    if incident_automatic:
        static_tools = [
            tool
            for tool in static_tools
            if _registered_tool_name(tool) not in INCIDENT_AUTOMATIC_EXCLUDED_TOOLS
        ]
    if guide is not None:
        walkthrough = walkthrough_tools(guide.mode, prefetch=guide_prefetch)
        # A prepare run works ahead of the reader in the background, so nothing it holds posts.
        static_tools = walkthrough if guide_prefetch else [*static_tools, *walkthrough]
    static_tools = apply_tool_descriptions(
        static_tools,
        {
            "expose_port": {
                "jwks_url": service_identity_jwks_url(),
                "port": "<port>",
            }
        },
    )
    if local_run:
        static_tools = apply_tool_descriptions([http_request, fetch_url, web_search])
    elif stop_summary_mode:
        static_tools = apply_tool_descriptions([slack_read_thread_messages, slack_reply])
    if prefer_tools_in_sandbox:
        static_tools = [
            tool for tool in static_tools if _registered_tool_name(tool) not in CURL_REPLACED_TOOLS
        ]
    reserved_tool_names = {_registered_tool_name(tool) for tool in static_tools}
    excluded_tools = (
        STOP_SUMMARY_EXCLUDED_TOOLS
        if stop_summary_mode
        else _slack_ask_excluded_tools(cfg)
        if slack_ask_mode
        else DEEP_AGENT_EXCLUDED_TOOLS | INCIDENT_AUTOMATIC_EXCLUDED_TOOLS
        if incident_automatic
        else DEEP_AGENT_EXCLUDED_TOOLS | GUIDE_PREFETCH_EXCLUDED_TOOLS
        if guide_prefetch
        else DEEP_AGENT_EXCLUDED_TOOLS
    )
    sandbox_only_tools = (
        frozenset(SANDBOX_ONLY_TOOLS)
        if prefer_tools_in_sandbox and not stop_summary_mode
        else frozenset()
    )
    excluded_tools |= sandbox_only_tools
    # A client's tool replaces any server tool of the same name, so the endpoint's
    # view of which calls the client runs matches the graph's.
    client_tool_names = frozenset(spec.name for spec in cfg.client_tools)
    client_tools = ClientToolsMiddleware(cfg.client_tools) if cfg.client_tools else None
    if client_tools is not None:
        excluded_tools = (excluded_tools | CLIENT_OWNED_SERVER_TOOLS) - client_tool_names
    main_tools = [
        tool for tool in static_tools if _registered_tool_name(tool) not in client_tool_names
    ]
    # Nothing is owed on a run the model cannot answer through: an automatic
    # incident sweep, for one, has the reply tool taken away on purpose.
    reply_tool_offered = _registered_tool_name(slack_reply) in reserved_tool_names - excluded_tools
    integration_tools = DynamicToolMiddleware(
        {"MCPs": mcp_tools},
        reserved_names={*DEEP_AGENT_TOOL_NAMES, *reserved_tool_names},
        model_visible=not prefer_tools_in_sandbox,
    )
    dynamic_tool_middleware = integration_tools if integration_tools.has_groups else None

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
        skill_routes[ORGANIZATION_SKILLS_ROUTE] = skills_backend(None)
        skill_sources = [ORGANIZATION_SKILLS_ROUTE, BUNDLED_SKILLS_ROUTE]
        if credential_login:
            skill_routes[USER_SKILLS_ROUTE] = skills_backend(credential_login)
            skill_sources.insert(0, USER_SKILLS_ROUTE)
        # Offloaded images live in PostgreSQL so they can be read without the sandbox.
        skill_routes[BLOBS_ROUTE] = StoreBackend(
            store=ThreadBlobs(thread_id),
            namespace=lambda _runtime, thread_id=thread_id: blob_namespace(thread_id),
        )
    agent_backend = CompositeBackend(default=backend, routes=skill_routes)
    main_model = _make_model_or_defer(model_id, use_gateway=use_gateway, **model_kwargs)
    requested_models = (
        available_requested_models(fable_enabled=fable_enabled)
        if (adaptive_model_routing or source == "slack")
        and not thread_settings.get("model_handoff_complete", bool(stored_model))
        and source in {"dashboard", "slack"}
        and not local_run
        and not stop_summary_mode
        and incident_session is None
        and not cfg.background_task_completion
        and not cfg.continued_from_thread_id
        else None
    )
    image_fallback: ImageModelFallbackMiddleware | None = None
    if (
        not model_supports_images(model_id)
        or any(not option["supports_images"] for option in (requested_models or {}).values())
        or (
            adaptive_model_routing
            and any(
                not model_supports_images(route_id) for route_id, _ in routing_defaults.values()
            )
        )
    ):
        vision_model = main_model
        if not model_supports_images(model_id):
            vision_model_id, vision_effort = default_vision_model_pair()
            vision_model = _make_model_or_defer(
                vision_model_id,
                use_gateway=use_gateway,
                **provider_model_kwargs(
                    vision_model_id, vision_effort, max_tokens=DEFAULT_LLM_MAX_TOKENS
                ),
            )
        image_fallback = ImageModelFallbackMiddleware(vision_model)
        if not model_supports_images(model_id):
            image_fallback.add_text_only_model(main_model)

    configurable["image_model_fallback_enabled"] = image_fallback is not None

    def requested_model_factory(
        requested_model: str, requested_effort: str | None
    ) -> BaseChatModel:
        option = available_requested_models(fable_enabled=fable_enabled)[requested_model]
        model = _make_model_or_defer(
            requested_model,
            use_gateway=use_gateway,
            **provider_model_kwargs(
                requested_model,
                requested_effort or option["default_effort"],
                max_tokens=DEFAULT_LLM_MAX_TOKENS,
            ),
        )
        if image_fallback is not None and not option["supports_images"]:
            image_fallback.add_text_only_model(model)
        fallback_middleware.register_fallback(model, make_fallback_model(requested_model))
        return model

    # Keep checkpointed routing tasks resumable after a handoff disables routing.
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
        if adaptive_model_routing
    }
    if image_fallback is not None:
        for route, model in routing_models.items():
            if not model_supports_images(routing_defaults[route][0]):
                image_fallback.add_text_only_model(model)
    model_selection = ModelSelectionMiddleware(
        routing_models,
        main_model,
        route_model_ids={
            **{route: routed_model_id for route, (routed_model_id, _) in routing_defaults.items()},
            "default": model_id,
        },
        routing_mode=model_routing_mode,
        requested_model_factory=requested_model_factory,
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
            tools=main_tools,
            subagents=[
                _general_purpose_subagent(
                    subagent_model,
                    tools=[
                        tool
                        for tool in static_tools
                        if tool is not save_user_settings
                        and _registered_tool_name(tool) not in sandbox_only_tools
                    ],
                    workspace_skills=workspace_skills,
                    dynamic_tools=dynamic_tool_middleware,
                    offloading=ConversationOffloadingMiddleware(subagent_model, agent_backend),
                    incident_middleware=IncidentMiddleware(incident_session)
                    if incident_session is not None
                    else None,
                    guard_middleware=_subagent_guard_middleware(local_run),
                    inherited_middleware_exclusions=(
                        check_message_queue_before_model.name,
                        deliver_event_matches_before_model.name,
                        model_selection.name,
                    ),
                ),
            ],
            skills=skill_sources,
            backend=agent_backend,
            state_schema=DesktopAgentState if local_run else None,
            middleware=cast(
                list[AgentMiddleware[Any, Any, Any]],
                [
                    FilesystemMiddleware(backend=agent_backend, offload_binary_content=True),
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
                        recent_thread_context_enabled=(
                            sender_profile.get("recent_thread_context_enabled") is True
                            if sender_profile
                            else False
                        ),
                        admin_workspaces=admin_thread,
                        sole_writer=tool_access.sole,
                        model_selection=model_selection,
                        routing_defaults=routing_defaults,
                        requested_models=requested_models,
                        saved_requested_model=thread_settings.get("requested_model"),
                        bridge_client=bridge_client,
                        prefer_tools_in_sandbox=prefer_tools_in_sandbox,
                    ),
                    *(
                        [
                            ReviewGuideMiddleware(
                                thread_id=thread_id, approve_ts=cfg.review_guide_approve_ts
                            )
                        ]
                        if guide is not None
                        else []
                    ),
                    TranscriptMiddleware(),
                    *([client_tools] if client_tools else []),
                    *(
                        [IncidentMiddleware(incident_session)]
                        if incident_session is not None
                        else []
                    ),
                    *([workspace_skills] if workspace_skills else []),
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
                    *([task_coordination] if task_coordination else []),
                    *(
                        []
                        if task_coordination and task_coordination.is_worker
                        else [refresh_github_proxy_before_model]
                    ),
                    *(
                        []
                        if stop_summary_mode
                        else [check_message_queue_before_model, deliver_event_matches_before_model]
                    ),
                    *(
                        []
                        if guide_prefetch
                        else [
                            RequireUserReplyMiddleware(
                                _registered_tool_name(slack_reply),
                                _registered_tool_name(slack_no_reply_needed),
                                initial_surface=(
                                    _initial_reply_surface(cfg)
                                    if reply_tool_offered
                                    else WEB_REPLY_SURFACE
                                ),
                                replies=GUIDE_REPLY_TOOLS if guide is not None else frozenset(),
                            )
                        ]
                    ),
                    *(
                        [RequireCliResultMiddleware(_registered_tool_name(cli_result))]
                        if cli_result_required
                        else []
                    ),
                    notify_step_limit_reached,
                    record_run_usage,
                    model_selection,
                    fallback_middleware,
                    *([image_fallback] if image_fallback else []),
                    *([dynamic_tool_middleware] if dynamic_tool_middleware else []),
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
            else _slack_ask_excluded_tools(cfg)
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
