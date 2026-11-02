"""What starting an Open SWE agent run works out, and what preparing its turns does.

Resolves the run's models, builds the curated tool list plus optional integrations,
and sets the rules MDA's middleware follows around each model call (``agent_catalog``).
All per-thread state lives in the sandbox + thread metadata; the agent itself is
stateless.
"""

# ruff: noqa: E402, PLC2701
import hashlib
import logging
import time
import warnings
from collections.abc import Awaitable, Mapping, Sequence
from functools import cache, cached_property, partial
from typing import Any, Final, Self
from uuid import UUID

from openswe.config import ENV

logger = logging.getLogger(__name__)

_MODEL_ROUTING_SPLIT = 0.5

from langgraph.graph.state import RunnableConfig
from langgraph.runtime import Runtime
from langgraph_sdk import get_client
from runtime_api import sandbox_setup
from runtime_api.catalog import (
    SEARCH_TOOLS,
    CliResultRules,
    InboxSpec,
    LoopRules,
    McpServerSpec,
    ReplyRules,
    RoutingRules,
    WorkStateRules,
)
from runtime_api.endpoint import QUICK_CALL_SECONDS
from runtime_api.loop import RunSender, RunStarted

warnings.filterwarnings("ignore", module="langchain_core._api.deprecation")

import asyncio
from dataclasses import replace

# Suppress Pydantic v1 compatibility warnings from langchain on Python 3.14+
warnings.filterwarnings("ignore", message=".*Pydantic V1.*", category=UserWarning)

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import BackendProtocol, SandboxBackendProtocol
from deepagents.backends.store import StoreBackend
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langchain_core.tools import tool as as_tool
from langchain_quickjs._ptc import is_valid_ptc_tool_name, to_camel_case
from langsmith.sandbox import SandboxRetryableConnectionError

from openswe.agent_api.connections import acting_user
from openswe.agent_api.tokens import AGENT_TURN_KEY, AgentTurnSettings
from openswe.analytics.usage import record_agent_invocation_usage
from openswe.bridge.cli_result import cli_result
from openswe.bridge.constants import LOCAL_ARTIFACTS_DIR, BridgeClient
from openswe.bridge.store import Bridge
from openswe.bridge.worktree_branch import schedule_worktree_branch_rename
from openswe.bridge.worktree_handoff import worktree_handoff
from openswe.credential_scope import private_credential_login
from openswe.dashboard.agent_overrides import (
    load_profile,
    profile_draft_prs,
    resolve_github_login,
)
from openswe.dashboard.options import (
    SUPPORTED_MODEL_IDS,
    available_requested_models,
    default_vision_model_pair,
    gate_fable_model,
    model_supports_effort,
    model_supports_images,
)
from openswe.dashboard.profiles import PROFILES
from openswe.dashboard.workspace_settings import WorkspaceSettings, get_workspace_settings
from openswe.dashboard.workspace_settings_cache import cached_workspace_settings
from openswe.github.token import resolve_github_token
from openswe.input_messages import (
    dynamic_context_hash,
    message_sender_id,
    person_introduction,
    visible_dynamic_context_hashes,
)
from openswe.mcp import load_mcp_tools
from openswe.mcp.instance import instance_mcp_source
from openswe.mcp.managed import instance_gateway, managed_mcp_source
from openswe.mcp.runtime import MCPTools, catalog_note
from openswe.mcp.user import user_mcp_source
from openswe.mcp.workspace import workspace_mcp_source
from openswe.memories.store import MEMORIES
from openswe.middleware import (
    BasePrepareRunMiddleware,
    DynamicToolMiddleware,
)
from openswe.middleware.client_tools import ClientToolsMiddleware
from openswe.middleware.model_selection import RoutingMode
from openswe.middleware.prepare_run import PrepareRunState
from openswe.middleware.require_cli_result import cli_result_nudge
from openswe.middleware.require_user_reply import (
    CARD_TOOLS,
    REPLY_GUARD,
    SLACK_REPLY_SURFACE,
    WEB_REPLY_SURFACE,
    WORK_STATE_GUARD,
    ReplySurface,
    reply_nudge,
    work_state_nudge,
)
from openswe.middleware.sandbox_circuit_breaker import post_sandbox_unreachable_notification
from openswe.middleware.stale_workspace import queue_stale_workspace_notice
from openswe.middleware.task_coordination import TaskCoordinationMiddleware
from openswe.model_request import (
    ModelSelectionDecision,
    infer_requested_model,
    model_selection_trace,
)
from openswe.openai_responses.client_tools import CLIENT_OWNED_SERVER_TOOLS
from openswe.prompt import SystemPrompt, TurnContext, construct_system_prompt
from openswe.prompts import apply_tool_descriptions, prompt, warm_tool_schemas
from openswe.review_guide.sessions import ReviewGuideSession
from openswe.run_config import RunConfig
from openswe.runtime.constants import (
    DEFAULT_LLM_MAX_TOKENS,
    MODEL_CALL_RECURSION_LIMIT,
)
from openswe.runtime.constants import (
    DEFAULT_LLM_MODEL_ID as DEFAULT_LLM_MODEL_ID,
)
from openswe.sandboxes.lifecycle import (
    ensure_sandbox_for_thread,
    get_cached_sandbox_backend,
    take_stale_boot,
)
from openswe.sandboxes.paths import resolve_sandbox_work_dir
from openswe.sandboxes.providers.langsmith import TimeoutLangSmithSandbox, service_identity_jwks_url
from openswe.sandboxes.retry import SANDBOX_ATTACH_MAX_ELAPSED, retry_transient_sandbox_errors
from openswe.sandboxes.state import (
    SandboxHandedOverError,
    SandboxUnreachableError,
    get_or_create_sandbox_backend_proxy,
    get_sandbox_metadata,
)
from openswe.sandboxes.tool_runtime import (
    FILESYSTEM_TOOL_NAMES,
    ModelChoice,
    SurfaceModels,
    SurfaceSubagent,
    ToolSurface,
)
from openswe.search_policy import SHELL_SEARCH_EXCLUDED_TOOLS
from openswe.skill_store.run_skills import RunSkillStores
from openswe.slack.client import get_slack_user_timezone, remember_slack_user_timezone
from openswe.slack.dm import is_concierge_thread, is_dm_channel
from openswe.slack.staged_files import deliver_slack_files, slack_files_pending
from openswe.thread_title import (
    ANTHROPIC_TITLE_GENERATION_MAX_TOKENS,
    TITLE_GENERATION_MAX_TOKENS,
    schedule_thread_title_generation,
)
from openswe.threads.blobs import ThreadBlobs, blob_namespace
from openswe.threads.recent_context import RecentContextAudience, recent_thread_context_section
from openswe.threads.summary import DASHBOARD_SOURCE
from openswe.tools import (
    assign_human_reviewer,
    auto_assign_human_reviewer,
    background_execute,
    background_task,
    code_channel_set_view,
    configure_repository,
    configure_workspace,
    connect_managed_tools,
    create_automation,
    create_sandbox_file_download_url,
    decide_memory,
    delete_automation,
    delete_organization_skill,
    delete_user_skill,
    delete_workspace,
    dismiss_human_review_request,
    execute_database_sql,
    expedite_pr_approval,
    expose_port,
    fetch_url,
    forget,
    get_human_review_status,
    get_thread,
    http_request,
    link_pull_request,
    list_automations,
    list_event_types,
    list_memories,
    list_threads,
    list_workspaces,
    listen_events,
    manage_baby_sit,
    manage_code_channel,
    manage_incident,
    manage_thread,
    merge_expedited_pr,
    monitor,
    open_pull_request,
    output_iframe,
    plan_walkthrough,
    publish_artifact,
    publish_workspace,
    publish_workspace_prompt,
    read_only_sql,
    read_user_settings,
    read_workspace_prompt,
    recreate_sandbox,
    remember,
    report_platform_issue,
    report_work_state,
    request_human_review,
    request_pr_review,
    request_rollout_check,
    save_organization_skill,
    save_user_settings,
    save_user_skill,
    schedule_thread_wakeup,
    search_pull_requests,
    send_announcement,
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
    snapshotctl,
    start_thread,
    submit_thread_feedback,
    suggest_task,
    switch_to_performance_model,
    trigger_automation,
    update_automation,
    web_search,
    write_database_rows,
)
from openswe.tools.access import permitted, resolve_access
from openswe.tools.add_review_comment import add_review_comment
from openswe.tools.admin_gate import (
    actor_has_admin_context,
    participant_is_admin,
)
from openswe.tools.manage_feature_flags import manage_feature_flags
from openswe.tools.manage_review_approval_mode import manage_review_approval_mode
from openswe.tools.manage_review_repos import manage_review_repos
from openswe.tools.note_on_diff import note_on_diff
from openswe.tools.propose_pr_review import propose_pr_review
from openswe.tools.propose_review_comment import propose_review_comment
from openswe.tools.review_walkthrough import walkthrough_tools
from openswe.tools.submit_review_assessment_feedback import submit_review_assessment_feedback
from openswe.tools.task_threads import (
    control_worker,
    message_task_thread,
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
from openswe.utils.thread_reads import read_thread
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
DEEP_AGENT_EXCLUDED_TOOLS = SHELL_SEARCH_EXCLUDED_TOOLS
# Each posts to the reader and ends the walkthrough's turn as surely as a final reply.
GUIDE_REPLY_TOOLS = frozenset(
    {
        "walkthrough_show_chunk",
        "walkthrough_show_lines",
        "walkthrough_show_other",
        "walkthrough_end",
    }
)
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


# Channel history also serves source-free requests; its own policy gates access.
SOURCE_FREE_SLACK_TOOLS: frozenset[str] = frozenset(
    {"slack_read_channel_messages", "slack_read_thread_messages"}
)


def _tool_description_values() -> dict[str, dict[str, object]]:
    return {"expose_port": {"jwks_url": service_identity_jwks_url(), "port": "<port>"}}


def warm_agent_build() -> None:
    """Pay a worker's one-time graph-building costs at startup instead of on its first run.

    That is every exported tool's schemas.
    """
    import openswe.tools as tools

    functions = [getattr(tools, name) for name in tools.__all__]
    warm_tool_schemas(functions, _tool_description_values())


def _registered_tool_name(value: Any) -> str:
    name = getattr(value, "name", None) or getattr(value, "__name__", None)
    if not isinstance(name, str) or not name:
        raise TypeError(f"tool has no registered name: {value!r}")
    return name


async def _resolve_prompt_default_repo(cfg: RunConfig) -> dict[str, str] | None:
    if cfg.repo:
        return {"owner": cfg.repo.owner, "name": cfg.repo.name}

    if cfg.repo_explicitly_none is True or cfg.experience == "agent_workspace":
        return None

    try:
        email = cfg.user_email or (
            cfg.slack_thread.triggering_user_email if cfg.slack_thread else None
        )
        user = (
            await User.for_login("github", cfg.github_login)
            if cfg.github_login
            else await User.for_email(email)
            if email
            else None
        )
        personal = await user.default_repo(workspace_slug(cfg)) if user else None
        return personal or (await get_workspace_settings(workspace_slug(cfg))).default_repo
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
        thread = await read_thread(thread_id, client)
        logins = participant_logins(thread_metadata(thread).get(PARTICIPANT_LOGINS_KEY))
        return await resolve_participant_identities(
            login for login in logins if not login.startswith("user:")
        )
    except Exception:
        logger.debug("Failed to resolve participant identities for %s", thread_id, exc_info=True)
        return []


async def _participant_user(login: str, person_id: str | None) -> User | None:
    """Resolve a participant by their canonical id before falling back to a surface login."""
    try:
        if person_id and person_id.startswith("user:"):
            user = await User.get(UUID(person_id.removeprefix("user:")))
            if user is not None:
                return user
        return await User.for_login("github", login) if login else None
    except Exception:
        logger.warning(
            "Could not resolve a participant; describing them from surface data",
            extra={"participant_login": login, "person_id": person_id},
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
    user = await _participant_user(identity.github_login, person_id)
    login = (user.github_login if user else "") or identity.github_login
    slack_user_id = slack_user_id or (user.slack_user_id if user else "")
    if timezone and slack_user_id:
        remember_slack_user_timezone(slack_user_id, timezone)
    elif slack_user_id:
        timezone = await get_slack_user_timezone(slack_user_id)
    if not login:
        instructions = await _resolve_user_custom_instructions(f"user:{user.id}") if user else None
        return ThreadParticipant(
            identity=replace(
                identity, display_name=user.display_name, commit_name="", commit_email=""
            )
            if user
            else identity,
            person_id=person_id or "",
            instructions=instructions or "",
            email=user.email if user else "",
            timezone=timezone,
            linked=user is not None,
            slack_user_id=slack_user_id,
        )
    identity = replace(identity, github_login=login)
    profile, workspace_admin, instructions, memories = await asyncio.gather(
        load_profile(login),
        participant_is_admin(login),
        _resolve_user_custom_instructions(login),
        MEMORIES.personal(
            user.id if user else None,
            curated_in=(
                RunConfig.from_config(config).workspace_slug
                if user is not None and user.typed_preferences.experimental_memory
                else None
            ),
        ),
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
        memories=tuple(memory.line for memory in memories),
        email=(user.email if user else "") or "",
        timezone=timezone,
        linked=user is not None,
        slack_user_id=slack_user_id,
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
        identity
        for identity in identities
        if identity.github_login.casefold() != resolved_sender.github_login.casefold()
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
        "monitor",
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
        "start_thread",
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
        "snapshotctl",
        "configure_repository",
        "configure_workspace",
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


def _is_subagent_excluded_tool(name: str) -> bool:
    """Return whether a tool requires the parent agent."""
    if name in SOURCE_FREE_SLACK_TOOLS:
        return False
    return name.startswith("slack_") or name in {
        "background_execute",
        "background_task",
        "monitor",
        # Only the parent's own turn can say where the work stands.
        "report_work_state",
        "submit_thread_feedback",
        "submit_review_assessment_feedback",
        # The review page reads both from the parent's transcript.
        "note_on_diff",
        "add_review_comment",
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
        "execute_database_sql",
        "read_user_settings",
        "connect_managed_tools",
        "save_user_settings",
        "remember",
        "forget",
        "list_memories",
        "read_workspace_prompt",
        "decide_memory",
        "publish_workspace_prompt",
        "record_incident_report",
        "search_incidents",
        "start_thread",
        "task",
    }


def fallback_model_choice(primary_model_id: str) -> ModelChoice | None:
    """The cross-provider model a call on ``primary_model_id`` falls back to, if any."""
    fallback_model_id = ENV.LLM_FALLBACK_MODEL_ID.optional() or fallback_model_id_for(
        primary_model_id
    )
    if not fallback_model_id or fallback_model_id == primary_model_id:
        return None
    kwargs: ModelKwargs = {"max_tokens": DEFAULT_LLM_MAX_TOKENS}
    if fallback_model_id.startswith("openai:"):
        kwargs["reasoning"] = DEFAULT_LLM_REASONING
    return ModelChoice(fallback_model_id, kwargs)


def _general_purpose_description() -> str:
    return (
        f"{GENERAL_PURPOSE_SUBAGENT['description']} "
        f"{prompt('system/general-purpose-subagent-suffix')}"
    )


# Workspace-admin tools; each declares where it may run with `@access`.
ADMIN_TOOLS = (
    list_automations,
    create_automation,
    update_automation,
    trigger_automation,
    delete_automation,
    list_workspaces,
    publish_workspace,
    snapshotctl,
    configure_repository,
    configure_workspace,
    delete_workspace,
    save_organization_skill,
    delete_organization_skill,
    send_announcement,
)


# Every tool the agent may offer; each run's plan offers those it keeps (``agent_catalog``),
# and a call answered without the plan finds its tool here by name.
AGENT_TOOLS: Final = (
    http_request,
    fetch_url,
    web_search,
    background_execute,
    background_task,
    monitor,
    publish_artifact,
    remember,
    forget,
    list_memories,
    read_workspace_prompt,
    decide_memory,
    publish_workspace_prompt,
    save_user_settings,
    save_user_skill,
    delete_user_skill,
    list_threads,
    search_pull_requests,
    get_thread,
    manage_thread,
    task_status,
    message_task_thread,
    control_worker,
    start_thread,
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
    output_iframe,
    create_sandbox_file_download_url,
    expose_port,
    read_user_settings,
    request_pr_review,
    connect_managed_tools,
    recreate_sandbox,
    report_platform_issue,
    schedule_thread_wakeup,
    report_work_state,
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
    note_on_diff,
    add_review_comment,
    *ADMIN_TOOLS,
    cli_result,
    worktree_handoff,
    read_only_sql,
    execute_database_sql,
    write_database_rows,
    manage_feature_flags,
    manage_review_approval_mode,
    manage_review_repos,
)


def workspace_slug(cfg: RunConfig) -> str | None:
    """The workspace this thread selected, if any."""
    return cfg.workspace_slug


async def _admin_thread(config: RunnableConfig, profile_login: str | None) -> bool:
    """Whether this run may manage workspaces and organization skills."""
    return await actor_has_admin_context(RunConfig.from_config(config), login=profile_login)


def _writes_database_rows() -> bool:
    """Whether this deployment's runs may repair data, which only preview and staging allow."""
    return ENV.OPENSWE_ENV.optional() in {"preview", "staging"}


@cache
def agent_tools_by_name() -> Mapping[str, BaseTool]:
    """Every tool this deployment's agent may offer, a review walkthrough's of either mode included.

    Each tool checks who may call it as it runs; what this deployment withholds from
    every run stays out of reach here too.
    """
    tools = [
        *(
            tool
            for tool in AGENT_TOOLS
            if _writes_database_rows() or tool not in (execute_database_sql, write_database_rows)
        ),
        *walkthrough_tools("reviewer"),
        *walkthrough_tools("author"),
        *apply_tool_descriptions(
            [
                plan_walkthrough.walkthrough_plan_chunk,
                plan_walkthrough.walkthrough_split_hunks,
                plan_walkthrough.walkthrough_move_to_other,
                plan_walkthrough.walkthrough_describe_other,
            ]
        ),
    ]
    built = (tool if isinstance(tool, BaseTool) else as_tool(tool) for tool in tools)
    return {tool.name: tool for tool in built}


# How long a process keeps the MCP tools it loaded for a person in a workspace, and
# for how many at once.
_INTEGRATIONS_TTL_SECONDS: Final = 300.0
_INTEGRATIONS_LIMIT: Final = 128
_integrations: dict[tuple[str | None, str], tuple[float, dict[str, BaseTool]]] = {}


def _keep_integrations(key: tuple[str | None, str], tools: dict[str, BaseTool]) -> None:
    """Keep ``tools`` for a while, dropping what expired and then the oldest past the limit."""
    now = time.monotonic()
    for stale in [k for k, (expires, _tools) in _integrations.items() if expires <= now]:
        del _integrations[stale]
    _integrations.pop(key, None)
    while len(_integrations) >= _INTEGRATIONS_LIMIT:
        del _integrations[next(iter(_integrations))]
    _integrations[key] = (now + _INTEGRATIONS_TTL_SECONDS, tools)


async def integration_tools(config: RunnableConfig) -> Mapping[str, BaseTool]:
    """The MCP tools the run may call, by name, loaded as its plan loads them."""
    cfg = RunConfig.from_config(config)
    try:
        login = await private_credential_login(config)
    except Exception:
        logger.exception("Cannot resolve thread credential scope; omitting MCP tools")
        return {}
    workspace = workspace_slug(cfg) or DEFAULT_WORKSPACE_SLUG
    loaded = _integrations.get((login, workspace))
    if loaded is not None and loaded[0] > time.monotonic():
        return loaded[1]
    _gateway, tools = await _run_mcp_tools(cfg.thread_id or "", login, workspace)
    by_name: dict[str, BaseTool] = {tool.name: tool for tool in tools if isinstance(tool, BaseTool)}
    _keep_integrations((login, workspace), by_name)
    return by_name


async def _reconnect_agent_sandbox(thread_id: str, workspace: str | None) -> SandboxBackendProtocol:
    return await ensure_sandbox_for_thread(thread_id, workspace_slug=workspace)


class ThreadFiles:
    """The thread's files as the agent's tools reach them: its sandbox, with the paths this
    deployment serves routed in.

    Those are the run's skill stores, and the images it offloaded, which PostgreSQL
    keeps so they can be read without the sandbox.
    """

    def __init__(
        self, thread_id: str, cfg: RunConfig, *, skills: RunSkillStores, bridged: bool
    ) -> None:
        # A bridged machine's run keeps its artifacts apart from the person's own files.
        self.artifacts_root = f"{LOCAL_ARTIFACTS_DIR}/{thread_id}/" if bridged else "/"
        self.routes: dict[str, BackendProtocol] = {
            **skills.routes,
            f"{self.artifacts_root}blobs/": StoreBackend(
                store=ThreadBlobs(thread_id),
                namespace=lambda _runtime: blob_namespace(thread_id),
            ),
        }
        cached = get_cached_sandbox_backend(
            thread_id, reconnect=partial(_reconnect_agent_sandbox, thread_id, workspace_slug(cfg))
        )
        self.backend = CompositeBackend(
            default=cached, routes=self.routes, artifacts_root=self.artifacts_root
        )

    @classmethod
    async def for_run(cls, config: RunnableConfig) -> Self:
        """The thread's files for a call answered without the run's plan."""
        cfg = RunConfig.from_config(config)
        if cfg.thread_id is None:
            raise ValueError("An agent run needs a thread")
        return cls(
            cfg.thread_id,
            cfg,
            skills=await RunSkillStores.for_run(config),
            bridged=await _bridge_client(cfg.thread_id) is not None,
        )


async def _bridge_client(thread_id: str | None) -> BridgeClient | None:
    """The app serving this thread's bridge to the user's machine, if it has one."""
    if not thread_id:
        return None
    try:
        thread = await read_thread(thread_id, client)
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
) -> MCPTools:
    """Load MCPs by precedence: instance, workspace, user, then the private owner's gateway."""
    sources = [instance_mcp_source(), workspace_mcp_source(workspace)]
    if credential_login:
        sources.append(user_mcp_source(credential_login))
        if managed_gateway:
            sources.append(managed_mcp_source(credential_login, managed_gateway))
    return await load_mcp_tools(*sources)


def _owned_mcp_servers(
    config: RunnableConfig, credential_login: str | None, servers: Sequence[McpServerSpec]
) -> list[McpServerSpec]:
    """``servers`` without the personal ones, unless the MDA run acts as their owner.

    MDA reads a personal server's connection as the person the run acts as there
    (``acting_user``), whom the run is also attributed to. A run that acts as no one, such
    as a wakeup or a background task's completion, has no person's principal, whoever it
    is attributed to: MDA starts it as a service principal, MDA's channel one
    (``mda:channel``) on a channel's thread and the backend key's own on any other, and
    MDA refuses user connections to a service principal, so a grant there can be neither
    made nor read.
    """
    metadata = config.get("metadata") or {}
    attributed = metadata.get("ls_attribution_identifier")
    for_owner = (
        acting_user(metadata) is not None
        and isinstance(attributed, str)
        and credential_login is not None
        and attributed.lower() == credential_login.lower()
    )
    owned: list[McpServerSpec] = []
    for server in servers:
        if server.connection.owner == "user" and not for_owner:
            logger.warning(
                "Leaving out a personal MCP server of a run that does not act for its owner",
                extra={"mcp_name": server.name},
            )
            continue
        owned.append(server)
    return owned


async def _run_mcp_tools(
    thread_id: str, credential_login: str | None, workspace: str
) -> tuple[str | None, MCPTools]:
    """The instance's Managed Tools gateway and the MCP tools the run may use."""
    managed_gateway = await instance_gateway() if credential_login else None
    async with aphase(thread_id, "factory.mcp_tools"):
        return managed_gateway, await _mcp_tools_for(credential_login, workspace, managed_gateway)


# The sandbox's file and shell tools, which code mode offers directly as well.
SANDBOX_TOOL_NAMES = frozenset({*FILESYSTEM_TOOL_NAMES, "delete", "background_execute"})
# Never called from code: subagents, the replies a run waits on, and tools whose
# results update graph state, which a call from code drops.
_NOT_FROM_CODE = (
    frozenset({"task"})
    | GUIDE_REPLY_TOOLS
    | frozenset(
        {
            "slack_reply",
            "slack_no_reply_needed",
            "cli_result",
            "connect_managed_tools",
            "switch_to_performance_model",
            "record_human_input",
            "load_integration_tools",
            "control_worker",
            "message_task_thread",
        }
    )
)
# Everyday tools code mode still offers the model directly.
_DIRECT_IN_CODE_MODE = SANDBOX_TOOL_NAMES | {
    SEARCH_TOOLS,
    "monitor",
    "open_pull_request",
    "web_search",
    "fetch_url",
    # A lesson is worth keeping the moment it is learned, not after a search for the tool.
    "remember",
    "forget",
}


def callable_from_code(name: str) -> bool:
    """Whether code mode can offer a tool as ``tools.<camelCaseName>``."""
    return name not in _NOT_FROM_CODE and is_valid_ptc_tool_name(name)


def direct_and_from_code(name: str) -> bool:
    """Whether code mode offers a tool from code while keeping its schema on the model's list."""
    return callable_from_code(name) and name in _DIRECT_IN_CODE_MODE


def code_name(name: str) -> str | None:
    """The name code mode calls a tool by, as ``tools.<name>``, if it can."""
    return to_camel_case(name) if callable_from_code(name) else None


_QUICK_CALLS = frozenset(
    {"get_thread", "list_threads", "slack_read_channel_messages", "slack_read_thread_messages"}
)


def call_seconds(name: str) -> float | None:
    """How long a call of a tool may take, when shorter than a tool call's allowance."""
    return QUICK_CALL_SECONDS if name in _QUICK_CALLS else None


def code_only(name: str) -> bool:
    """Whether code mode offers a tool only from code, keeping its schema off the model's list."""
    return callable_from_code(name) and name not in _DIRECT_IN_CODE_MODE


def _integration_middleware(
    mcp_tools: Sequence[BaseTool], reserved_names: set[str]
) -> DynamicToolMiddleware | None:
    candidate = DynamicToolMiddleware({"MCPs": mcp_tools}, reserved_names=reserved_names)
    return candidate if candidate.has_groups else None


async def _timed[T](thread_id: str | None, name: str, work: Awaitable[T]) -> T:
    async with aphase(thread_id, name):
        return await work


async def _credential_scope(config: RunnableConfig) -> tuple[str | None, bool]:
    """Whose personal integrations the run may use, and whether that could be told."""
    try:
        return await private_credential_login(config), True
    except Exception:
        logger.exception("Cannot resolve thread credential scope; omitting MCP tools")
        return None, False


async def _thread_settings(thread_id: str) -> tuple[ThreadSettings, bool]:
    return normalize_thread_settings(await load_thread_settings(client, thread_id))


async def _scoped_mcp_tools(
    thread_id: str, scope: Awaitable[tuple[str | None, bool]], workspace: str
) -> tuple[str | None, MCPTools]:
    login, known = await scope
    return await _run_mcp_tools(thread_id, login, workspace) if known else (None, MCPTools())


async def _cached_profile(profile_login: str | None):
    if not profile_login:
        return None
    return await ttl_cache.cached(
        PROFILES.cache_key(profile_login), 30, lambda: load_profile(profile_login)
    )


def _sandbox_file_downloads_enabled(*, bridged: bool) -> bool:
    """Return whether signed sandbox file downloads are available for this run.

    They are served by the LangSmith sandbox itself, which a bridged thread does not have.
    """
    return not bridged


def _initial_reply_surface(cfg: RunConfig) -> ReplySurface:
    """Where this run owes its answer, before anything moves mid-run."""
    if cfg.source in {DASHBOARD_SOURCE, "github"} or not cfg.slack_context:
        return WEB_REPLY_SURFACE
    return SLACK_REPLY_SURFACE


def _slack_concierge_run(cfg: RunConfig) -> bool:
    """Whether this run answers in a bot DM its owner runs in concierge mode."""
    return (
        cfg.slack_context
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
    """Ready an agent run's turn: its prompt, model and route, and who it speaks with.

    Built from what starting the run decided (``AgentTurnSettings``), so preparing a turn
    plans nothing. A thread whose sandbox MDA keeps (``sandbox``, its name) is not reached here:
    its work dir is the sandbox's own, and MDA readied the sandbox as it opened it.
    """

    def __init__(
        self,
        config: RunnableConfig,
        turn: AgentTurnSettings,
        *,
        repo_instructions: str | None,
        sandbox_id: str | None = None,
    ) -> None:
        cfg = RunConfig.from_config(config)
        if cfg.thread_id is None:
            raise ValueError("An agent run needs a thread")
        self._config = config
        self._turn = turn
        self._thread_id = cfg.thread_id
        self._sandbox_id = sandbox_id
        self._bridge_client = turn.bridge_client
        self._saved_requested_model = turn.saved_requested_model
        self._requested_models = (
            available_requested_models(fable_enabled=turn.fable_enabled)
            if turn.offers_requested_models
            else None
        )
        self._profile_login = turn.profile_login
        self._credential_login = turn.credential_login
        self._repo_instructions = repo_instructions
        self._model_id = cfg.resolved_agent_model_id or ""
        self._effort = cfg.resolved_agent_effort
        self._source = cfg.source or "dashboard"
        self._user_email = cfg.user_email or ""
        self._draft_prs = cfg.draft_prs is True
        self._admin_workspaces = turn.admin_workspaces
        self._sole_writer = turn.sole_writer
        self._routing_defaults = dict(turn.route_models)
        self._tool_catalog_note = catalog_note(turn.tool_discovery_failures)

    @cached_property
    def _title_model(self) -> BaseChatModel:
        turn = self._turn
        anthropic = turn.title_model_id.startswith("anthropic:")
        kwargs = provider_model_kwargs(
            turn.title_model_id,
            turn.title_effort,
            max_tokens=(
                ANTHROPIC_TITLE_GENERATION_MAX_TOKENS if anthropic else TITLE_GENERATION_MAX_TOKENS
            ),
        )
        if anthropic:
            kwargs.pop("thinking", None)
        return _make_model_or_defer(turn.title_model_id, use_gateway=turn.use_gateway, **kwargs)

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
                    },
                    error=None if prepared else "Model selection preparation failed",
                )

    async def _rejoin(self, state: PrepareRunState, runtime: Runtime) -> None:  # noqa: ARG002
        await resolve_github_token(self._config, self._thread_id)

    async def _attach_sandbox(
        self, identity: asyncio.Task[CollaboratorIdentity | None], bound: Mapping[str, object]
    ) -> tuple[CollaboratorIdentity | None, SandboxBackendProtocol]:
        """Connect to the sandbox the thread is ``bound`` to, alongside ``identity``."""
        sandbox_proxy = get_or_create_sandbox_backend_proxy(self._thread_id)
        sandbox_proxy.follow(bound.get("sandbox_id"))
        sandbox_task = asyncio.create_task(
            retry_transient_sandbox_errors(
                sandbox_proxy.ready,
                description="Sandbox attach",
                max_elapsed=SANDBOX_ATTACH_MAX_ELAPSED,
            )
        )
        try:
            async with aphase(self._thread_id, "prepare.await_sandbox"):
                return await asyncio.gather(identity, sandbox_task)
        except (SandboxUnreachableError, SandboxRetryableConnectionError) as exc:
            # The run is about to die with no sandbox; make sure the user hears
            # why rather than getting silence.
            await post_sandbox_unreachable_notification(
                self._config or {},
                sandbox_id=exc.sandbox_id if isinstance(exc, SandboxUnreachableError) else None,
                handed_to_mda=isinstance(exc, SandboxHandedOverError),
            )
            raise

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
        if self._requested_models is not None:
            settings = (await load_thread_settings(client, self._thread_id)).copy()
            if not settings.get("model_handoff_complete"):
                handoff_config = RunConfig.from_config(self._config)
                handoff = await infer_requested_model(
                    messages=state.get("messages") or [],
                    requested_models=self._requested_models,
                    performance_model=self._routing_defaults.get("performance"),
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
                if decision.outcome != "reused_saved_choice":
                    decision.outcome = "accepted_request"
                    decision.reason = "validated_and_persisted"
                self._model_id = requested_model
                self._effort = requested_effort
        configurable = (self._config or {}).get("configurable") or {}
        configurable["draft_prs"] = self._draft_prs
        cfg = RunConfig.parse(configurable)
        # A thread's system prompt is rendered on its first run and never again.
        render_prompt = not (
            state.get("system_prompt_fixed") and state.get("rendered_system_prompt")
        )
        async with aphase(self._thread_id, "prepare.github_token"):
            github_token, _expires_at = await resolve_github_token(self._config, self._thread_id)
        triggering_user_identity_task = asyncio.create_task(
            resolve_triggering_user_identity(as_json_object(self._config), github_token)
        )
        bound = await get_sandbox_metadata(self._thread_id)
        if self._sandbox_id is not None and not slack_files_pending(bound):
            triggering_user_identity = await triggering_user_identity_task
            work_dir, bridged = sandbox_setup.WORK_DIR, False
        else:
            triggering_user_identity, sandbox_backend = await self._attach_sandbox(
                triggering_user_identity_task, bound
            )
            if isinstance(sandbox_backend, TimeoutLangSmithSandbox):
                sandbox_backend.reset_git_identity()
            async with aphase(self._thread_id, "prepare.slack_files"):
                await deliver_slack_files(self._thread_id, bound, sandbox_backend)
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
        del github_token
        if stale_workspace := take_stale_boot(self._thread_id):
            queue_stale_workspace_notice(self._thread_id, stale_workspace)
        rendered_prompt = (
            asyncio.create_task(self._render_system_prompt(cfg)) if render_prompt else None
        )
        # Reads only who the run is for, so it runs alongside participants.
        recent_threads = asyncio.create_task(self._recent_threads(cfg))
        async with aphase(self._thread_id, "prepare.participants"):
            configurable["resolved_agent_model_id"] = self._model_id
            configurable["resolved_agent_effort"] = self._effort
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
        turn = TurnContext(
            working_dir=work_dir,
            agent_workspace=cfg.experience == "agent_workspace",
            source="background_task" if cfg.background_task_completion else self._source,
            slack_context=cfg.slack_context,
            slack_ask=cfg.slack_ask_mode,
            slack_by_the_way=cfg.slack_ask_mode and bool(cfg.slack_by_the_way_thread_ts),
            slack_breakout=cfg.slack_breakout is True,
            slack_follow_up_suggestions=_slack_concierge_run(cfg)
            or (await cached_workspace_settings(workspace_slug(cfg))).get(
                "slack_follow_up_suggestions"
            )
            is True,
            goal_mode=bool(self._profile_login)
            and (await User.preferences_for_login(self._profile_login)).goal_mode,
            implementation_walkthrough_enabled=(
                await cached_workspace_settings(workspace_slug(cfg))
            ).get("implementation_walkthrough_enabled")
            is True,
            sandbox_file_downloads=_sandbox_file_downloads_enabled(bridged=bridged),
            local_checkout=bridged,
            local_checkout_client=self._bridge_client or "cli",
            tool_catalog_note=self._tool_catalog_note,
            recent_threads=await recent_threads,
        )
        added = [*sender_messages, *turn.messages(visible_dynamic_context_hashes(state))]
        system_prompt = (await rendered_prompt).state() if rendered_prompt is not None else {}
        auto_reset = (
            cfg.source == "dashboard"
            and cfg.model_selection == "auto"
            and cfg.model_selection_changed
        )
        return {
            "work_dir": work_dir,
            "requested_model": requested_model,
            "requested_effort": requested_effort,
            "selected_model_id": self._model_id,
            "selected_effort": self._effort,
            **({"messages": added} if added else {}),
            # Switching back to auto routes the thread afresh: MDA's loop picks its route again.
            **({"model_route": None} if auto_reset else {}),
            **system_prompt,
        }

    async def _recent_threads(self, cfg: RunConfig) -> str:
        audience = self._recent_context_audience(cfg)
        if audience is None:
            return ""
        async with aphase(self._thread_id, "prepare.recent_threads"):
            return await recent_thread_context_section(
                audience=audience,
                login=self._profile_login,
                email=self._user_email or None,
                exclude_thread_id=self._thread_id,
                slack_team_id=(cfg.slack_thread.team_id if cfg.slack_thread else None),
                slack_channel_id=(cfg.slack_thread.channel_id if cfg.slack_thread else None),
            )

    async def _render_system_prompt(self, cfg: RunConfig) -> SystemPrompt:
        """The thread's system prompt, from what holds for it as its first run starts."""
        async with aphase(self._thread_id, "prepare.default_repo"):
            default_repo = await _resolve_prompt_default_repo(cfg)
        async with aphase(self._thread_id, "prepare.workspace"):
            workspace = await load_workspace(workspace_slug(cfg))
            memory = (
                await MEMORIES.workspace_prompt(workspace.slug)
                if workspace
                and self._profile_login
                and (await User.preferences_for_login(self._profile_login)).experimental_memory
                else ""
            )
        return construct_system_prompt(
            agent_workspace=cfg.experience == "agent_workspace",
            dashboard_base_url=dashboard_base_url(),
            artifact_url=dashboard_plan_url(self._thread_id),
            default_repo=default_repo,
            repo_custom_instructions=self._repo_instructions,
            workspace_name=workspace.name if workspace else None,
            workspace_instructions=workspace.instructions if workspace else None,
            workspace_repos=workspace.repos if workspace else None,
            workspace_memory=memory,
            admin_workspaces=self._admin_workspaces,
            sole_writer=self._sole_writer,
            continued_from_collaborative=bool(cfg.continued_from_thread_id),
        )


def agent_sender(config: RunnableConfig, turn: AgentTurnSettings) -> RunSender:
    """Who an agent run's invocation is attributed to: whoever sent what started it."""
    cfg = RunConfig.from_config(config)
    return RunSender(
        github_login=cfg.github_login if cfg.auth_provider == "langsmith" else turn.profile_login,
        github_user_id=cfg.github_user_id,
        user_email=cfg.user_email,
        source=cfg.source or "dashboard",
        repository=cfg.repo_full_name or None,
    )


async def record_agent_run(
    config: RunnableConfig, turn: AgentTurnSettings, started: RunStarted, *, carried: bool
) -> None:
    """Record what an agent run's turn runs as: on the thread, and in usage.

    Each is recorded whether or not the other could be: usage records the run's completion
    only once its start is there. A ``carried`` start, an earlier invocation's that this run
    sends, is recorded in usage as its own sender's, and leaves the thread to this run.
    """
    cfg = RunConfig.from_config(config)
    thread_id = cfg.thread_id
    if thread_id is None:
        return
    sender = started.sender or (None if carried else agent_sender(config, turn))
    # A routed run runs as its route's model, which MDA picked after preparation.
    route = started.model_route
    routed = turn.route_models.get(route) if route is not None and route != "default" else None
    if routed is not None:
        model_id, effort = routed
    else:
        model_id = started.model_id or (None if carried else cfg.resolved_agent_model_id)
        effort = started.effort or (None if carried else cfg.resolved_agent_effort)
    source = (sender.source if sender is not None else None) or "dashboard"

    async def on_thread() -> None:
        try:
            await client.threads.update(
                thread_id=thread_id,
                metadata={
                    "agent_kind": "agent",
                    "model": model_id,
                    "effort": effort,
                    "source": source,
                    **(
                        {"model_route": started.model_route}
                        if started.model_route is not None
                        else {}
                    ),
                },
            )
        except Exception:
            logger.warning(
                "Failed to record the agent run on its thread",
                extra={"agent_thread_id": thread_id},
                exc_info=True,
            )

    async def in_usage(sender: RunSender, model_id: str, invocation_id: str) -> None:
        identity: CollaboratorIdentity | None = None
        if not carried:
            # This token's sender is the invocation's only when the run sends its own start.
            try:
                github_token, _expires_at = await resolve_github_token(config, thread_id)
                identity = await resolve_triggering_user_identity(
                    as_json_object(config), github_token
                )
            except Exception:
                logger.warning(
                    "Failed to resolve the agent run's sender for usage",
                    extra={"agent_thread_id": thread_id},
                    exc_info=True,
                )
        try:
            await record_agent_invocation_usage(
                invocation_id=invocation_id,
                thread_id=thread_id,
                github_login=sender.github_login,
                github_user_id=identity.github_user_id
                if identity is not None and identity.github_user_id is not None
                else sender.github_user_id,
                user_email=sender.user_email or "",
                display_name=identity.analytics_display_name
                if identity is not None and identity.analytics_display_name
                else None,
                display_name_source=identity.display_name_source if identity is not None else None,
                model_id=model_id,
                effort=effort,
                source=source,
                repository=sender.repository,
            )
        except Exception:
            logger.warning(
                "Failed to record the agent run's usage",
                extra={"agent_thread_id": thread_id},
                exc_info=True,
            )

    recording = [] if carried else [on_thread()]
    if sender is not None and model_id is not None and cfg.invocation_id:
        recording.append(in_usage(sender, model_id, cfg.invocation_id))
    elif carried:
        logger.warning(
            "Not recording a carried run start that names no sender",
            extra={"agent_thread_id": thread_id, "invocation_id": cfg.invocation_id},
        )
    async with aphase(thread_id, "run_started.record_run"):
        await asyncio.gather(*recording)


async def agent_catalog(config: RunnableConfig, surface: ToolSurface) -> None:
    """Work out an agent run into ``surface``: its tools, models, subagents and loop rules."""
    configurable = config.get("configurable") or {}
    cfg = RunConfig.parse(configurable)
    thread_id = cfg.thread_id
    if thread_id is None:
        raise ValueError("An agent run needs a thread")

    from openswe.incidents.runtime import IncidentSession, load_incident_session

    incident_session: IncidentSession | None = None
    if cfg.source == "incidents_agent":
        incident_session = await load_incident_session(config, run_id=None)
        cfg.slack_thread = incident_session.slack_thread
        configurable["slack_thread"] = cfg.slack_thread.dump()
    credential_scope = asyncio.ensure_future(_credential_scope(config))
    # MCP loading needs only who the run acts for and where, and it is the slowest
    # lookup here, so it overlaps every lookup below.
    mcp_tools_task = asyncio.ensure_future(
        _scoped_mcp_tools(
            thread_id, credential_scope, workspace_slug(cfg) or DEFAULT_WORKSPACE_SLUG
        )
    )
    (
        walkthrough,
        profile_login,
        (credential_login, _),
        task_coordination,
        bridge_client,
        (thread_settings, settings_changed),
    ) = await asyncio.gather(
        ReviewGuideSession.get(thread_id),
        resolve_github_login(as_json_object(config)),
        credential_scope,
        TaskCoordinationMiddleware.for_thread(thread_id),
        _timed(thread_id, "factory.bridged_thread", _bridge_client(thread_id)),
        _timed(thread_id, "factory.thread_settings", _thread_settings(thread_id)),
    )
    guide = walkthrough if walkthrough is not None and not walkthrough.closed else None

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
    # Workspace/profile settings are accepted stale for a short TTL so graph factories
    # stay off the critical path during worker load and retry storms.
    settings: WorkspaceSettings | None = None
    routing_defaults: dict[str, tuple[str, str | None]]
    (settings, profile), admin_thread, tool_access = await asyncio.gather(
        _timed(
            thread_id,
            "factory.settings_defaults",
            asyncio.gather(
                cached_workspace_settings(settings_workspace),
                _cached_profile(
                    profile_login
                    if reset_model_selection or not thread_settings.get("model_id")
                    else None
                ),
            ),
        ),
        _timed(thread_id, "factory.admin_thread", _admin_thread(config, profile_login)),
        _timed(thread_id, "factory.tool_access", resolve_access(cfg, login=profile_login)),
    )
    model_defaults = settings.default_model_pair("agent")
    routing_defaults = dict(settings.agent_routing_models)
    title_defaults = settings.default_thread_title_model
    use_gateway = settings.effective_gateway_enabled
    fable_enabled = settings.fable_enabled

    slack_ask_mode = cfg.slack_ask_mode

    (model_id, profile_effort), (subagent_model_id, subagent_effort) = model_defaults
    for route, stored_route in thread_settings.get("routing_models", {}).items():
        if route in routing_defaults:
            routing_defaults[route] = (stored_route["model_id"], stored_route["effort"])
    title_model_id, title_effort = title_defaults
    logger.info("Using workspace default agent model: model=%s effort=%s", model_id, profile_effort)

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
    elif (
        cfg.source == "dashboard"
        and cfg.model_selection == "auto"
        and (reset_model_selection or not isinstance(stored_model, str))
    ):
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
    goal_mode = bool(profile_login) and (
        (await User.preferences_for_login(profile_login)).goal_mode
    )
    work_state_tool = _registered_tool_name(report_work_state)
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
    if settings_changed or {**thread_settings, **resolved_settings} != thread_settings:
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

    source = cfg.source or "dashboard"
    configurable["source"] = source
    configurable["resolved_agent_model_id"] = model_id
    configurable["resolved_agent_effort"] = profile_effort

    cli_result_required = bridge_client == "cli"
    sandbox_file_downloads = _sandbox_file_downloads_enabled(bridged=bridge_client is not None)
    managed_gateway, mcp_tools = await mcp_tools_task
    if incident_session is not None:
        # incident.io refuses publishing from this MCP client (surface_cannot_publish).
        mcp_tools = [
            tool
            for tool in mcp_tools
            if (tool.metadata or {}).get("mcp_tool_name") != "status_page_update"
        ]

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
        slack_reply,
        slack_breakout_thread,
        slack_start_review_channel,
    ]
    withheld = [
        *(() if goal_mode else (report_work_state,)),
        *(() if task_coordination else (task_status, message_task_thread, control_worker)),
        *((start_thread,) if task_coordination and task_coordination.is_worker else ()),
        *(() if managed_gateway else (connect_managed_tools,)),
        *(
            ()
            if sandbox_file_downloads
            else (output_iframe, create_sandbox_file_download_url, expose_port)
        ),
        *(() if cli_result_required else (cli_result,)),
        *(() if bridge_client == "desktop" else (worktree_handoff,)),
        *(() if _writes_database_rows() else (execute_database_sql, write_database_rows)),
    ]
    static_tools = [tool for tool in AGENT_TOOLS if not any(tool is w for w in withheld)]
    static_tools = permitted(static_tools, tool_access)
    if not cfg.slack_context:
        # An automation run has no Slack thread, but its prompt may ask it to
        # report to a channel.
        kept = (slack_list_channels, slack_post_message) if cfg.source == "schedule" else ()
        static_tools = [tool for tool in static_tools if tool not in slack_tools or tool in kept]
    elif _slack_concierge_run(cfg):
        static_tools = [
            tool for tool in static_tools if _registered_tool_name(tool) not in DM_EXCLUDED_TOOLS
        ]
    if not (await cached_workspace_settings(settings_workspace)).expedited_review_enabled:
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
    if guide is None and settings.get("implementation_walkthrough_enabled") is True:
        static_tools = [
            *static_tools,
            plan_walkthrough.walkthrough_plan_chunk,
            plan_walkthrough.walkthrough_split_hunks,
            plan_walkthrough.walkthrough_move_to_other,
            plan_walkthrough.walkthrough_describe_other,
        ]
    if guide is not None:
        static_tools = [*static_tools, *walkthrough_tools(guide.mode)]
    static_tools = apply_tool_descriptions(static_tools, _tool_description_values())
    reserved_tool_names = {_registered_tool_name(tool) for tool in static_tools}
    excluded_tools = (
        _slack_ask_excluded_tools(cfg)
        if slack_ask_mode
        else DEEP_AGENT_EXCLUDED_TOOLS | INCIDENT_AUTOMATIC_EXCLUDED_TOOLS
        if incident_automatic
        else DEEP_AGENT_EXCLUDED_TOOLS
    )
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
    dynamic_tool_middleware = _integration_middleware(
        mcp_tools,
        {*DEEP_AGENT_TOOL_NAMES, *reserved_tool_names},
    )

    logger.info("Returning agent with sandbox for thread %s", thread_id)
    skill_stores = RunSkillStores(credential_login)
    files = ThreadFiles(thread_id, cfg, skills=skill_stores, bridged=bridge_client is not None)
    requested_models = (
        available_requested_models(fable_enabled=fable_enabled)
        if (adaptive_model_routing or source == "slack")
        and not thread_settings.get("model_handoff_complete", bool(stored_model))
        and source in {"dashboard", "slack"}
        and incident_session is None
        and not cfg.background_task_completion
        and not cfg.continued_from_thread_id
        else None
    )
    # The model a call to a text-only model goes to instead once the conversation
    # holds an image; the MDA run makes the swap.
    vision_model: ModelChoice | None = None
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
        vision_model = ModelChoice(model_id, dict(model_kwargs))
        if not model_supports_images(model_id):
            vision_model_id, vision_effort = default_vision_model_pair()
            vision_model = ModelChoice(
                vision_model_id,
                dict(
                    provider_model_kwargs(
                        vision_model_id, vision_effort, max_tokens=DEFAULT_LLM_MAX_TOKENS
                    )
                ),
            )

    configurable["image_model_fallback_enabled"] = vision_model is not None

    route_model_ids = {
        **{route: routed_model_id for route, (routed_model_id, _) in routing_defaults.items()},
        "default": model_id,
    }
    # What preparing each turn reads, signed into the run's token (``RunSettings.agent``).
    turn = AgentTurnSettings(
        profile_login=profile_login,
        credential_login=credential_login,
        admin_workspaces=admin_thread,
        sole_writer=tool_access.sole,
        title_model_id=title_model_id,
        title_effort=title_effort,
        use_gateway=bool(use_gateway),
        routing=RoutingRules(mode=model_routing_mode, route_model_ids=route_model_ids),
        route_models=routing_defaults,
        offers_requested_models=requested_models is not None,
        fable_enabled=fable_enabled,
        saved_requested_model=thread_settings.get("requested_model"),
        bridge_client=bridge_client,
        review_guide=guide is not None,
        review_guide_closed=walkthrough is not None and walkthrough.closed,
        task_worker=task_coordination is not None and task_coordination.is_worker,
        tool_discovery_failures=list(mcp_tools.failures) if isinstance(mcp_tools, MCPTools) else [],
    )
    configurable[AGENT_TURN_KEY] = turn.model_dump(mode="json")
    subagent_tools = [
        tool
        for tool in static_tools
        if _registered_tool_name(tool) != _registered_tool_name(save_user_settings)
    ]
    initial_surface = _initial_reply_surface(cfg) if reply_tool_offered else WEB_REPLY_SURFACE
    surface.base_tools = [*main_tools, *(client_tools.tools if client_tools else [])]
    surface.model_call_limit = (
        incident_session.policy.max_model_calls
        if incident_session is not None
        else MODEL_CALL_RECURSION_LIMIT
    )
    surface.delegates = task_coordination is not None and task_coordination.can_delegate
    reply_tool = _registered_tool_name(slack_reply)
    no_reply_tool = _registered_tool_name(slack_no_reply_needed)
    cli_result_tool = _registered_tool_name(cli_result)
    surface.loop = LoopRules(
        hidden_tools=sorted(excluded_tools),
        routing=RoutingRules(mode=model_routing_mode, route_model_ids=route_model_ids),
        reply=ReplyRules(
            tool=reply_tool,
            no_reply_tool=no_reply_tool,
            surface=initial_surface,
            replies=sorted(
                {
                    *CARD_TOOLS,
                    *(GUIDE_REPLY_TOOLS if guide is not None else ()),
                    *((work_state_tool,) if goal_mode else ()),
                }
            ),
            nudge=reply_nudge(reply_tool, no_reply_tool),
            nudge_sender=REPLY_GUARD["id"],
        ),
        cli_result=CliResultRules(tool=cli_result_tool, nudge=cli_result_nudge(cli_result_tool))
        if cli_result_required
        else None,
        work_state=WorkStateRules(
            tool=work_state_tool,
            waits=[
                _registered_tool_name(tool)
                for tool in (schedule_thread_wakeup, background_execute, monitor)
            ],
            nudge=work_state_nudge(work_state_tool),
            nudge_sender=WORK_STATE_GUARD["id"],
        )
        if goal_mode
        else None,
        task_coordination=task_coordination is not None,
        client_tools=client_tools is not None,
        refresh_github_proxy=not (task_coordination and task_coordination.is_worker),
        incident=incident_session is not None,
        offload_conversation=cfg.offload_conversation,
    )
    surface.inbox = InboxSpec(
        events=True,
        surface=initial_surface,
        model_id=model_id,
        image_fallback=vision_model is not None,
    )
    surface.artifacts_root = files.artifacts_root
    surface.file_checks = ("image_reads", "agents_md")
    surface.models = SurfaceModels(
        main=ModelChoice(model_id, dict(model_kwargs)),
        subagent=ModelChoice(
            subagent_model_id,
            dict(model_kwargs if subagent_model_id == model_id else subagent_model_kwargs),
        ),
        use_gateway=bool(use_gateway),
        routes={
            route: ModelChoice(
                routed_model_id,
                dict(
                    provider_model_kwargs(
                        routed_model_id, effort, max_tokens=DEFAULT_LLM_MAX_TOKENS
                    )
                ),
            )
            for route, (routed_model_id, effort) in routing_defaults.items()
            if adaptive_model_routing
        },
        vision=vision_model,
    )
    surface.subagents = [
        SurfaceSubagent(
            name=GENERAL_PURPOSE_SUBAGENT["name"],
            description=_general_purpose_description(),
            system_prompt=GENERAL_PURPOSE_SUBAGENT["system_prompt"],
            tools=(
                *FILESYSTEM_TOOL_NAMES,
                *(
                    name
                    for name in map(_registered_tool_name, subagent_tools)
                    if not _is_subagent_excluded_tool(name)
                ),
            ),
        )
    ]
    surface.dynamic = dynamic_tool_middleware
    surface.mcp_servers = _owned_mcp_servers(
        config,
        credential_login,
        mcp_tools.runtime_servers if isinstance(mcp_tools, MCPTools) else [],
    )
    # The runtime lists the skills and answers reads of them itself (``runtime_api.skills``).
    surface.skill_sources = skill_stores.sources
    surface.backend_paths = tuple(
        route for route in files.routes if route not in skill_stores.sources
    )
    surface.code_mode = True
    surface.excluded = (
        _slack_ask_excluded_tools(cfg)
        if slack_ask_mode
        else DEEP_AGENT_EXCLUDED_TOOLS | INCIDENT_AUTOMATIC_EXCLUDED_TOOLS
        if incident_automatic
        else DEEP_AGENT_EXCLUDED_TOOLS
    )
