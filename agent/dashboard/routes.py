"""FastAPI router for the dashboard backend."""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any, Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .. import (
    delivery_queue,
    linear_queue,
    project_model_endpoints,
    project_model_routing,
    project_registry,
    project_secrets,
)
from .admin import is_admin
from .agent_instructions import (
    AgentInstructionsCreate,
    AgentInstructionsUpdate,
    create_agent_instructions,
    delete_agent_instructions,
    get_agent_instructions,
    list_agent_instructions,
    set_agent_instructions,
)
from .agent_usage import (
    list_agent_usage_leaderboard,
    refresh_reviewer_stats_cache,
    refresh_usage_leaderboard_cache,
)
from .analyzer_cron import remove_continual_cron
from .enabled_repos import (
    list_enabled_review_repos,
    set_review_repo_enabled,
)
from .eval_jobs import (
    get_reviewer_eval_status,
)
from .notion_oauth import (
    NOTION_STATE_COOKIE_NAME,
    NotionOAuthError,
    exchange_notion_code,
    pop_notion_oauth_flow,
    store_notion_oauth_flow,
)
from .oauth import (
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
    STATE_COOKIE_NAME,
    STATE_TTL_SECONDS,
    decode_state,
    enforce_org_login_gate,
    exchange_code,
    fetch_github_user,
    hash_state_nonce,
    issue_session,
    issue_state,
    new_state_nonce,
    require_same_origin_for_mutations,
    require_session,
    sanitize_redirect_to,
)
from .options import SUPPORTED_MODELS
from .password_auth import (
    authenticate_password,
    create_password_reset_token,
    request_password_reset,
    reset_password,
    set_password_account_enabled,
    upsert_password_account,
)
from .profiles import (
    ProfileUpdate,
    get_profile,
    get_valid_access_token,
    upsert_access_token_from_github_response,
    upsert_profile,
)
from .provider_pat_vault import (
    get_provider_pat_status,
    list_provider_pat_status,
    resolve_provider_pat,
    revoke_provider_pat,
    upsert_provider_pat,
)
from .repo_access import require_repo_access_for_user
from .repo_snapshots import (
    RepoSnapshotConfigError,
    RepoSnapshotCreate,
    RepoSnapshotUpdate,
    create_repo_snapshot,
    delete_repo_snapshot,
    generate_dockerfile_template,
    get_repo_snapshot,
    is_repo_snapshot_build_stale,
    list_repo_snapshots,
    mark_repo_snapshot_building,
    run_snapshot_build,
    update_repo_snapshot,
)
from .review_api import (
    create_review_comment,
    dry_run_trace_resolution,
    get_review,
    get_review_diff,
    list_review_comments,
    list_reviews,
    proxy_pr_image,
    trigger_re_review,
)
from .review_chat_api import (
    delete_review_chat_thread,
    get_review_chat,
    list_review_chat_threads,
    proxy_review_chat_commands,
    proxy_review_chat_history,
    proxy_review_chat_state,
    proxy_review_chat_stream_events,
)
from .review_style_jobs import (
    cancel_review_style_analysis,
    start_bootstrap_analysis,
    sync_review_style_run_status,
)
from .review_styles import (
    ReviewStyleCreate,
    ReviewStylePromptUpdate,
    create_review_style,
    delete_review_style,
    get_review_style,
    list_review_styles,
    normalize_repo_full_name,
    set_custom_prompt,
)
from .schedules import (
    ScheduleCreateBody,
    ScheduleUpdateBody,
    create_agent_schedule,
    delete_agent_schedule,
    list_agent_schedules,
    update_agent_schedule,
)
from .slack_oauth import (
    SLACK_STATE_COOKIE_NAME,
    build_authorize_url,
    exchange_slack_code,
    fetch_slack_identity,
    slack_oauth_configured,
    verify_team,
)
from .team_credentials import (
    DatadogCredentialsUpdate,
    LangSmithCredentialsUpdate,
    connect_datadog,
    connect_langsmith,
    disconnect_datadog,
    disconnect_langsmith,
    get_team_credentials_status,
)
from .team_settings import (
    TeamSettingsUpdate,
    get_team_default_model,
    get_team_default_subagent_model,
    get_team_settings,
    upsert_team_settings,
)
from .thread_api import (
    ThreadMessageBody,
    ThreadResolveBody,
    cancel_dashboard_thread,
    delete_dashboard_thread,
    get_dashboard_thread,
    get_dashboard_thread_pr_diff,
    get_dashboard_thread_recovery_patch,
    get_dashboard_thread_state,
    list_dashboard_threads,
    list_dashboard_threads_page,
    list_dashboard_threads_sidebar,
    proxy_dashboard_thread_commands,
    proxy_dashboard_thread_history,
    proxy_dashboard_thread_run_cancel,
    proxy_dashboard_thread_stream_events,
    resolve_dashboard_thread,
    send_dashboard_message,
    stream_dashboard_thread,
)
from .user_credentials import (
    CurrentsCredentialsUpdate,
    connect_currents,
    connect_notion,
    disconnect_currents,
    disconnect_notion,
    get_currents_status,
    get_notion_status,
)
from .user_mappings import (
    delete_mapping,
    get_mapping,
    list_mappings,
    upsert_mapping,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/dashboard/api",
    tags=["dashboard"],
    dependencies=[Depends(require_same_origin_for_mutations)],
)
_GITHUB_API_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
_SKIPPABLE_INSTALLATION_REPO_STATUS_CODES = frozenset({403, 404})


class PasswordLoginBody(BaseModel):
    email: str
    password: str


class PasswordResetRequestBody(BaseModel):
    email: str


class PasswordResetConfirmBody(BaseModel):
    token: str
    password: str


class PasswordAccountCreateBody(BaseModel):
    login: str
    email: str
    password: str
    enabled: bool = True


class PasswordAccountEnabledBody(BaseModel):
    enabled: bool


class ProviderPATUpdateBody(BaseModel):
    token: str


class ProjectSecretUpdateBody(BaseModel):
    environment: str = project_secrets.DEFAULT_AI_HUB_ENVIRONMENT
    value: str
    kind: str = "api_key"


class ProjectSecretTestBody(BaseModel):
    environment: str = project_secrets.DEFAULT_AI_HUB_ENVIRONMENT


class AIHubImportBody(BaseModel):
    environment: str = project_secrets.DEFAULT_AI_HUB_ENVIRONMENT
    prefixes: list[str] | None = None


class TicketIntakeUpdateBody(BaseModel):
    provider: Literal["linear"] = "linear"
    team_ids: list[str] = Field(default_factory=list)
    team_keys: list[str] = Field(default_factory=list)
    team_names: list[str] = Field(default_factory=list)
    linear_project_ids: list[str] = Field(default_factory=list)
    linear_project_names: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=lambda: ["agent-ready"])
    ready_states: list[str] = Field(default_factory=lambda: ["ready"])
    excluded_statuses: list[str] = Field(
        default_factory=lambda: ["done", "completed", "canceled", "cancelled", "duplicate"]
    )
    required_fields: list[str] = Field(default_factory=lambda: ["description"])
    missing_readiness: Literal["skip", "not-ready"] = "not-ready"
    polling_interval_minutes: int = 5


class RepositorySettingsUpdateBody(BaseModel):
    provider: Literal["github"] = "github"
    repositories: list[str] = Field(default_factory=list)
    default_repository: str
    base_branch: str = "main"
    branch_prefix: str = "delivery"
    draft_pull_requests: bool = True
    allowed_actions: list[str] = Field(default_factory=lambda: ["branch", "commit", "pull_request"])
    context_repositories: list[str] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)


class DeliveryPolicyUpdateBody(BaseModel):
    active: bool = True
    kill_switch: bool = False
    agent_review: bool = True
    qa_evidence: bool = True
    blocking_gates: list[str] = Field(default_factory=list)
    advisory_gates: list[str] = Field(default_factory=list)
    max_concurrent_runs: int = 1
    daily_run_budget: int = 10
    merge_enabled: bool = False
    merge_strategy: str = "squash"
    required_checks: list[str] = Field(default_factory=list)
    delete_branch: bool = True
    target_branch: str = ""


class ModelEndpointUpdateBody(BaseModel):
    id: str | None = None
    display_name: str
    provider_type: str
    base_url: str
    api_path: str = "/chat/completions"
    auth_type: Literal["bearer", "api_key", "none"] = "bearer"
    secret_name: str = ""
    default_headers: dict[str, str] = Field(default_factory=dict)
    model_ids: list[str] = Field(default_factory=list)
    model_capabilities: dict[str, dict[str, Any]] = Field(default_factory=dict)
    organization: str = ""
    project: str = ""
    timeout_seconds: int = 60
    rate_limit: dict[str, int] = Field(default_factory=dict)
    supports_model_discovery: bool = True
    disabled: bool = False


class ModelEndpointPresetCreateBody(BaseModel):
    provider_type: str
    environment: str = project_secrets.DEFAULT_AI_HUB_ENVIRONMENT


class ModelRoutingUpdateBody(BaseModel):
    environment: str = project_secrets.DEFAULT_AI_HUB_ENVIRONMENT
    default: dict[str, Any] | None = None
    roles: dict[str, dict[str, Any]] = Field(default_factory=dict)
    fallback: dict[str, Any] | None = None


def _session_is_admin(session: dict[str, Any]) -> bool:
    return is_admin(session.get("email"), login=session.get("sub"))


def _require_admin(session: dict[str, Any]) -> dict[str, Any]:
    if not _session_is_admin(session):
        raise HTTPException(403, "admin only")
    return session


_SESSION_DEP = Depends(require_session)


def _admin_session(session: dict[str, Any] = _SESSION_DEP) -> dict[str, Any]:
    return _require_admin(session)


_ADMIN_DEP = Depends(_admin_session)


def _project_member_logins(project: dict[str, Any]) -> set[str]:
    membership = project.get("membership")
    if not isinstance(membership, dict):
        return set()
    users = membership.get("users")
    if not isinstance(users, list):
        return set()
    logins: set[str] = set()
    for user in users:
        if isinstance(user, str) and user.strip():
            logins.add(user.strip().lower())
        elif isinstance(user, dict):
            login = user.get("login") or user.get("github_login")
            if isinstance(login, str) and login.strip():
                logins.add(login.strip().lower())
    return logins


def _delivery_queue_summary(item: dict[str, Any]) -> dict[str, Any]:
    work_item = item.get("work_item") if isinstance(item.get("work_item"), dict) else {}
    delivery = item.get("delivery") if isinstance(item.get("delivery"), dict) else {}
    return {
        "id": item.get("id"),
        "status": item.get("status"),
        "title": item.get("title") or work_item.get("title"),
        "provider": item.get("provider"),
        "external_work_item_id": item.get("external_work_item_id"),
        "thread_id": item.get("thread_id") or delivery.get("thread_id"),
        "pull_request_url": item.get("pull_request_url")
        or delivery.get("pull_request_url")
        or delivery.get("pr_url"),
        "updated_at": item.get("updated_at"),
    }


def _clean_strings(values: list[str]) -> list[str]:
    return [value.strip() for value in values if isinstance(value, str) and value.strip()]


def _global_linear_token() -> str:
    return os.environ.get("LINEAR_API_KEY", "").strip()


async def _linear_token_for_session(
    session: dict[str, Any],
    *,
    project_id: str,
    action: str,
) -> str | None:
    resolved = await resolve_provider_pat(
        str(session["sub"]),
        provider="linear",
        project_id=project_id,
        action=action,
    )
    if resolved is not None:
        return resolved.token
    return _global_linear_token() or None


async def _ticket_intake_payload(project: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    tracker = project.get("tracker") if isinstance(project.get("tracker"), dict) else {}
    tracker_config = tracker.get("config") if isinstance(tracker.get("config"), dict) else {}
    queue_policy = (
        project.get("queue_eligibility_policy")
        if isinstance(project.get("queue_eligibility_policy"), dict)
        else {}
    )
    labels = _clean_strings(queue_policy.get("labels") or tracker_config.get("labels") or [])
    token = await _linear_token_for_session(
        session,
        project_id=str(project.get("project_id") or ""),
        action="ticket_intake_status",
    )
    source = "LINEAR_API_KEY" if token and token == _global_linear_token() else "provider_pat"
    return {
        "provider": str(tracker.get("provider") or "linear"),
        "credential": {
            "provider": "linear",
            "available": bool(token),
            "source": source if token else None,
        },
        "tracker_config": {
            "team_ids": _clean_strings(tracker_config.get("team_ids") or []),
            "team_keys": _clean_strings(tracker_config.get("team_keys") or []),
            "team_names": _clean_strings(tracker_config.get("team_names") or []),
            "linear_project_ids": _clean_strings(
                tracker_config.get("linear_project_ids") or tracker_config.get("project_ids") or []
            ),
            "linear_project_names": _clean_strings(
                tracker_config.get("linear_project_names")
                or tracker_config.get("project_names")
                or []
            ),
        },
        "queue_eligibility_policy": {
            "labels": labels or ["agent-ready"],
            "ready_states": _clean_strings(queue_policy.get("ready_states") or ["ready"]),
            "excluded_statuses": _clean_strings(queue_policy.get("excluded_statuses") or []),
            "required_fields": _clean_strings(queue_policy.get("required_fields") or []),
            "missing_readiness": queue_policy.get("missing_readiness") or "skip",
            "polling_interval_minutes": int(queue_policy.get("polling_interval_minutes") or 5),
        },
    }


def _ticket_intake_update_payload(
    project: dict[str, Any],
    body: TicketIntakeUpdateBody,
) -> dict[str, Any]:
    if body.polling_interval_minutes != 5:
        raise HTTPException(422, "V1 polling interval must remain 5 minutes")
    tracker_config = {
        "team_ids": _clean_strings(body.team_ids),
        "team_keys": _clean_strings(body.team_keys),
        "team_names": _clean_strings(body.team_names),
        "linear_project_ids": _clean_strings(body.linear_project_ids),
        "linear_project_names": _clean_strings(body.linear_project_names),
    }
    if not any(tracker_config.values()):
        raise HTTPException(422, "at least one Linear team or project selector is required")
    labels = _clean_strings(body.labels)
    if not labels:
        raise HTTPException(422, "at least one ready label is required")
    return {
        "project_id": str(project["project_id"]),
        "tracker": {"provider": body.provider, "config": tracker_config},
        "queue_eligibility_policy": {
            "labels": labels,
            "ready_states": _clean_strings(body.ready_states) or ["ready"],
            "excluded_statuses": _clean_strings(body.excluded_statuses),
            "required_fields": _clean_strings(body.required_fields),
            "missing_readiness": body.missing_readiness,
            "polling_interval_minutes": body.polling_interval_minutes,
        },
    }


def _normalize_repository_names(values: list[str], *, field_name: str) -> list[str]:
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            full_name = normalize_repo_full_name(value)
        except ValueError as exc:
            raise HTTPException(422, f"{field_name}: {exc}") from exc
        if full_name not in normalized:
            normalized.append(full_name)
    return normalized


def _repository_settings_payload(
    project: dict[str, Any],
    *,
    access_statuses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    vcs = project.get("vcs") if isinstance(project.get("vcs"), dict) else {}
    vcs_config = vcs.get("config") if isinstance(vcs.get("config"), dict) else {}
    branch_policy = (
        project.get("branch_policy") if isinstance(project.get("branch_policy"), dict) else {}
    )
    credential_policy = (
        project.get("credential_policy")
        if isinstance(project.get("credential_policy"), dict)
        else {}
    )
    context_pack = (
        project.get("context_pack") if isinstance(project.get("context_pack"), dict) else {}
    )
    default_repository = ""
    if _has_mapping_values(vcs_config, "owner", "repo"):
        default_repository = f"{vcs_config['owner']}/{vcs_config['repo']}"
    repositories = _normalize_repository_names(
        [
            *(
                vcs_config.get("repositories")
                if isinstance(vcs_config.get("repositories"), list)
                else []
            ),
            default_repository,
        ],
        field_name="repositories",
    )
    context_repositories = _normalize_repository_names(
        context_pack.get("repositories")
        if isinstance(context_pack.get("repositories"), list)
        else [],
        field_name="context_repositories",
    )
    documents = _clean_strings(context_pack.get("documents") or [])
    return {
        "provider": str(vcs.get("provider") or "github"),
        "repositories": repositories,
        "default_repository": default_repository,
        "branch_policy": {
            "base_branch": branch_policy.get("base_branch") or "main",
            "branch_prefix": branch_policy.get("branch_prefix") or "delivery",
            "draft_pull_requests": branch_policy.get("draft_pull_requests") is not False,
        },
        "credential_policy": {
            "provider": credential_policy.get("provider") or "github",
            "requires_user_pat": credential_policy.get("requires_user_pat") is True,
            "allowed_actions": _clean_strings(credential_policy.get("allowed_actions") or []),
        },
        "context_pack": {
            "repositories": context_repositories,
            "required_documents": documents,
        },
        "access": access_statuses
        or [
            {
                "full_name": full_name,
                "default": full_name == default_repository,
                "status": "unchecked",
            }
            for full_name in repositories
        ],
    }


async def _repository_access_statuses(
    project: dict[str, Any],
    session: dict[str, Any],
) -> list[dict[str, Any]]:
    payload = _repository_settings_payload(project)
    statuses: list[dict[str, Any]] = []
    for full_name in payload["repositories"]:
        record = {
            "full_name": full_name,
            "default": full_name == payload["default_repository"],
            "status": "ready",
            "message": "Repository access verified.",
        }
        try:
            await require_repo_access_for_user(session["sub"], full_name)
        except HTTPException as exc:
            record["status"] = "blocked"
            record["message"] = str(exc.detail)
        statuses.append(record)
    return statuses


def _repository_settings_update_payload(
    project: dict[str, Any],
    body: RepositorySettingsUpdateBody,
) -> dict[str, Any]:
    default_repository = normalize_repo_full_name(body.default_repository)
    repositories = _normalize_repository_names(
        [*body.repositories, default_repository],
        field_name="repositories",
    )
    context_repositories = _normalize_repository_names(
        body.context_repositories or repositories,
        field_name="context_repositories",
    )
    allowed_actions = _clean_strings(body.allowed_actions)
    if not allowed_actions:
        raise HTTPException(422, "at least one allowed delivery action is required")
    base_branch = body.base_branch.strip()
    branch_prefix = body.branch_prefix.strip()
    if not base_branch:
        raise HTTPException(422, "base branch is required")
    if not branch_prefix:
        raise HTTPException(422, "branch prefix is required")
    owner, repo = default_repository.split("/", 1)
    existing_context = (
        project.get("context_pack") if isinstance(project.get("context_pack"), dict) else {}
    )
    return {
        "project_id": str(project["project_id"]),
        "vcs": {
            "provider": body.provider,
            "config": {"owner": owner, "repo": repo, "repositories": repositories},
        },
        "branch_policy": {
            "base_branch": base_branch,
            "branch_prefix": branch_prefix,
            "draft_pull_requests": body.draft_pull_requests,
        },
        "credential_policy": {
            **(
                project.get("credential_policy")
                if isinstance(project.get("credential_policy"), dict)
                else {}
            ),
            "provider": body.provider,
            "allowed_actions": allowed_actions,
        },
        "context_pack": {
            **existing_context,
            "repositories": context_repositories,
            "documents": _clean_strings(body.required_documents),
        },
    }


def _delivery_policy_payload(project: dict[str, Any]) -> dict[str, Any]:
    gate_policy = project.get("gate_policy") if isinstance(project.get("gate_policy"), dict) else {}
    merge_policy = (
        project.get("merge_policy") if isinstance(project.get("merge_policy"), dict) else {}
    )
    run_limits = project.get("run_limits") if isinstance(project.get("run_limits"), dict) else {}
    branch_policy = (
        project.get("branch_policy") if isinstance(project.get("branch_policy"), dict) else {}
    )
    return {
        "project_id": project.get("project_id"),
        "active": bool(project.get("active", True)),
        "kill_switch": bool(project.get("kill_switch", False)),
        "gate_policy": {
            "agent_review": gate_policy.get("agent_review") is not False,
            "qa_evidence": gate_policy.get("qa_evidence") is True,
            "blocking_gates": _clean_strings(gate_policy.get("blocking_gates") or []),
            "advisory_gates": _clean_strings(gate_policy.get("advisory_gates") or []),
        },
        "merge_policy": {
            "enabled": merge_policy.get("enabled") is True,
            "strategy": str(merge_policy.get("strategy") or "squash"),
            "required_checks": _clean_strings(merge_policy.get("required_checks") or []),
            "delete_branch": merge_policy.get("delete_branch") is not False,
            "target_branch": str(
                merge_policy.get("target_branch")
                or merge_policy.get("base_branch")
                or branch_policy.get("base_branch")
                or "main"
            ),
        },
        "run_limits": {
            "max_concurrent_runs": int(run_limits.get("max_concurrent_runs") or 1),
            "daily_run_budget": int(run_limits.get("daily_run_budget") or 10),
        },
    }


def _delivery_policy_update_payload(
    project: dict[str, Any],
    body: DeliveryPolicyUpdateBody,
) -> dict[str, Any]:
    blocking_gates = _clean_strings(body.blocking_gates)
    advisory_gates = _clean_strings(body.advisory_gates)
    required_checks = _clean_strings(body.required_checks)
    strategy = body.merge_strategy.strip()
    target_branch = body.target_branch.strip()
    if body.qa_evidence and not blocking_gates:
        raise HTTPException(
            422, "at least one blocking gate is required when QA evidence is enabled"
        )
    if body.max_concurrent_runs < 1:
        raise HTTPException(422, "max concurrent runs must be positive")
    if body.daily_run_budget < 1:
        raise HTTPException(422, "daily run budget must be positive")
    if body.merge_enabled and not strategy:
        raise HTTPException(422, "merge strategy is required when auto-merge is enabled")
    branch_policy = (
        project.get("branch_policy") if isinstance(project.get("branch_policy"), dict) else {}
    )
    return {
        "project_id": str(project["project_id"]),
        "active": body.active,
        "kill_switch": body.kill_switch,
        "gate_policy": {
            "agent_review": body.agent_review,
            "qa_evidence": body.qa_evidence,
            "blocking_gates": blocking_gates,
            "advisory_gates": advisory_gates,
        },
        "merge_policy": {
            "enabled": body.merge_enabled,
            "strategy": strategy or "squash",
            "required_checks": required_checks,
            "delete_branch": body.delete_branch,
            "target_branch": target_branch or str(branch_policy.get("base_branch") or "main"),
        },
        "run_limits": {
            "max_concurrent_runs": body.max_concurrent_runs,
            "daily_run_budget": body.daily_run_budget,
        },
    }


def _delivery_project_summary(
    project: dict[str, Any],
    *,
    latest_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "project_id": project.get("project_id"),
        "name": project.get("name"),
        "active": bool(project.get("active", True)),
        "kill_switch": bool(project.get("kill_switch", False)),
        "tracker": project.get("tracker") if isinstance(project.get("tracker"), dict) else {},
        "vcs": project.get("vcs") if isinstance(project.get("vcs"), dict) else {},
        "queue_eligibility_policy": (
            project.get("queue_eligibility_policy")
            if isinstance(project.get("queue_eligibility_policy"), dict)
            else {}
        ),
        "sandbox_profile": (
            project.get("sandbox_profile")
            if isinstance(project.get("sandbox_profile"), dict)
            else {}
        ),
        "branch_policy": (
            project.get("branch_policy") if isinstance(project.get("branch_policy"), dict) else {}
        ),
        "gate_policy": (
            project.get("gate_policy") if isinstance(project.get("gate_policy"), dict) else {}
        ),
        "merge_policy": (
            project.get("merge_policy") if isinstance(project.get("merge_policy"), dict) else {}
        ),
        "run_limits": project.get("run_limits")
        if isinstance(project.get("run_limits"), dict)
        else {},
        "member_logins": sorted(_project_member_logins(project)),
        "delivery_modes": (
            list(project.get("delivery_modes"))
            if isinstance(project.get("delivery_modes"), list)
            else []
        ),
        "latest_runs": [_delivery_queue_summary(item) for item in (latest_runs or [])],
    }


def _can_read_delivery_project(project: dict[str, Any], session: dict[str, Any]) -> bool:
    if _session_is_admin(session):
        return True
    login = str(session.get("sub") or "").strip().lower()
    return bool(login and login in _project_member_logins(project))


def _readiness_check(
    *,
    key: str,
    label: str,
    ready: bool,
    section: str,
    message: str,
    blockers: list[dict[str, Any]] | None = None,
    action_href: str | None = None,
    action_label: str | None = None,
) -> dict[str, Any]:
    check = {
        "key": key,
        "label": label,
        "ready": ready,
        "section": section,
        "message": message,
        "blockers": blockers or [],
    }
    if action_href:
        check["action_href"] = action_href
    if action_label:
        check["action_label"] = action_label
    return check


def _has_mapping_values(value: Any, *keys: str) -> bool:
    if not isinstance(value, dict):
        return False
    return all(isinstance(value.get(key), str) and value.get(key).strip() for key in keys)


def _project_readiness_environment(project: dict[str, Any]) -> str:
    ai_hub_policy = project.get("ai_hub_policy")
    if isinstance(ai_hub_policy, dict):
        environment = ai_hub_policy.get("environment")
        if isinstance(environment, str) and environment.strip():
            return environment.strip()
    return project_secrets.DEFAULT_AI_HUB_ENVIRONMENT


async def _delivery_project_readiness(
    project: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any]:
    project_id = str(project.get("project_id") or "")
    tracker = project.get("tracker") if isinstance(project.get("tracker"), dict) else {}
    tracker_config = tracker.get("config") if isinstance(tracker.get("config"), dict) else {}
    vcs = project.get("vcs") if isinstance(project.get("vcs"), dict) else {}
    vcs_config = vcs.get("config") if isinstance(vcs.get("config"), dict) else {}
    sandbox_profile = (
        project.get("sandbox_profile") if isinstance(project.get("sandbox_profile"), dict) else {}
    )
    queue_policy = (
        project.get("queue_eligibility_policy")
        if isinstance(project.get("queue_eligibility_policy"), dict)
        else {}
    )
    credential_policy = (
        project.get("credential_policy")
        if isinstance(project.get("credential_policy"), dict)
        else {}
    )
    gate_policy = project.get("gate_policy") if isinstance(project.get("gate_policy"), dict) else {}
    merge_policy = (
        project.get("merge_policy") if isinstance(project.get("merge_policy"), dict) else {}
    )
    run_limits = project.get("run_limits") if isinstance(project.get("run_limits"), dict) else {}
    model_routing = (
        project.get("model_routing") if isinstance(project.get("model_routing"), dict) else {}
    )
    ai_hub_policy = (
        project.get("ai_hub_policy") if isinstance(project.get("ai_hub_policy"), dict) else {}
    )

    tracker_ready = bool(tracker.get("provider")) and bool(tracker_config)
    tracker_provider = str(tracker.get("provider") or "linear").strip().lower()
    linear_token = (
        await _linear_token_for_session(
            session,
            project_id=project_id,
            action="readiness",
        )
        if tracker_provider == "linear"
        else "not-required"
    )
    tracker_credential_ready = tracker_provider != "linear" or bool(linear_token)
    repo_config_ready = bool(vcs.get("provider")) and _has_mapping_values(
        vcs_config, "owner", "repo"
    )
    repository_payload = _repository_settings_payload(project)
    default_repository = repository_payload.get("default_repository")
    repository_access_statuses = (
        await _repository_access_statuses(project, session) if repo_config_ready else []
    )
    default_repository_access = next(
        (
            status
            for status in repository_access_statuses
            if status.get("full_name") == default_repository
        ),
        None,
    )
    repo_ready = (
        repo_config_ready
        and isinstance(default_repository_access, dict)
        and default_repository_access.get("status") == "ready"
    )
    repo_blockers = []
    if not repo_config_ready:
        repo_blockers.append(
            {
                "code": "missing_repository_config",
                "message": "Configure the workspace repository owner and name.",
            }
        )
    elif not repo_ready:
        repo_blockers.append(
            {
                "code": "repository_access_blocked",
                "message": str(
                    (default_repository_access or {}).get("message")
                    or "Default repository access is blocked."
                ),
            }
        )
    required_pat = credential_policy.get("requires_user_pat") is True
    provider = str(credential_policy.get("provider") or vcs.get("provider") or "github")
    pat_status = (
        await get_provider_pat_status(str(session["sub"]), provider=provider)
        if required_pat
        else {"connected": True, "provider": provider}
    )
    environment = _project_readiness_environment(project)
    project_secret_statuses = await project_secrets.list_project_secrets(
        project_id,
        environment=environment,
    )
    required_secret_names = [
        str(name)
        for name in ai_hub_policy.get("required_secrets", [])
        if isinstance(name, str) and name.strip()
    ]
    connected_secret_names = {
        str(secret.get("name"))
        for secret in project_secret_statuses
        if secret.get("connected") is True
    }
    missing_secrets = [name for name in required_secret_names if name not in connected_secret_names]
    ai_hub_ready = (
        await project_secrets.evaluate_ai_hub_readiness(project_id, environment=environment)
        if ai_hub_policy.get("enabled") is True
        else {"ready": True, "blockers": [], "environment": environment}
    )
    sandbox_ready = bool(sandbox_profile.get("provider")) and (
        bool(sandbox_profile.get("profile")) or isinstance(sandbox_profile.get("runtime"), dict)
    )
    model_roles = model_routing.get("roles") if isinstance(model_routing.get("roles"), dict) else {}
    model_validation = await project_model_routing.validate_project_model_routing_ready(project)
    model_ready = (
        bool(project.get("delivery_modes"))
        and isinstance(model_roles, dict)
        and bool(model_validation.get("ready"))
    )
    queue_ready = bool(queue_policy.get("labels") or queue_policy.get("ready_states"))
    auto_mode_ready = (
        bool(project.get("active", True))
        and not bool(project.get("kill_switch", False))
        and isinstance(run_limits.get("max_concurrent_runs"), int)
        and run_limits.get("max_concurrent_runs", 0) > 0
        and isinstance(run_limits.get("daily_run_budget"), int)
        and run_limits.get("daily_run_budget", 0) > 0
    )
    blocking_gates = gate_policy.get("blocking_gates")
    qa_ready = (
        bool(gate_policy.get("qa_evidence"))
        and isinstance(blocking_gates, list)
        and bool(blocking_gates)
    )
    merge_ready = (
        merge_policy.get("enabled") is True
        and bool(merge_policy.get("strategy"))
        and isinstance(merge_policy.get("required_checks", []), list)
    )

    checks = [
        _readiness_check(
            key="tracker_intake",
            label="Tracker intake",
            ready=tracker_ready,
            section="ticket-intake",
            message="Linear intake is configured."
            if tracker_ready
            else "Configure the Linear workspace, project, labels, and readiness states.",
        ),
        _readiness_check(
            key="tracker_provider_token",
            label="Tracker provider token",
            ready=tracker_credential_ready,
            section="credentials",
            message="Linear provider token is connected."
            if tracker_credential_ready
            else "Connect a Linear provider token for queue polling.",
            action_href="/my-settings",
            action_label="Open profile settings",
        ),
        _readiness_check(
            key="repository_access",
            label="Repository access",
            ready=repo_ready,
            section="repositories",
            message="Default repository access is verified."
            if repo_ready
            else "Verify the default repository and current user provider token.",
            blockers=repo_blockers,
        ),
        _readiness_check(
            key="user_provider_token",
            label="User provider token",
            ready=bool(pat_status.get("connected")),
            section="credentials",
            message=f"{provider} user token is connected."
            if pat_status.get("connected")
            else f"Connect a {provider} provider token for this user.",
            action_href="/my-settings",
            action_label="Open profile settings",
        ),
        _readiness_check(
            key="project_secrets",
            label="Project secrets",
            ready=not missing_secrets,
            section="credentials",
            message="Required project secrets are present."
            if not missing_secrets
            else f"Missing project secrets: {', '.join(missing_secrets)}.",
        ),
        _readiness_check(
            key="ai_hub",
            label="AI Hub",
            ready=bool(ai_hub_ready.get("ready")),
            section="credentials",
            message="AI Hub credentials are ready."
            if ai_hub_ready.get("ready")
            else "AI Hub readiness failed.",
            blockers=list(ai_hub_ready.get("blockers") or []),
        ),
        _readiness_check(
            key="sandbox_profile",
            label="Sandbox profile",
            ready=sandbox_ready,
            section="overview",
            message="Sandbox profile is configured."
            if sandbox_ready
            else "Configure a sandbox profile or runtime.",
        ),
        _readiness_check(
            key="model_routing",
            label="Model routing",
            ready=model_ready,
            section="models",
            message="Delivery modes and model routing are configured."
            if model_ready
            else "Configure delivery modes and model routing.",
            blockers=list(model_validation.get("blockers") or []),
        ),
        _readiness_check(
            key="queue_policy",
            label="Queue policy",
            ready=queue_ready,
            section="ticket-intake",
            message="Queue eligibility policy is configured."
            if queue_ready
            else "Configure readiness labels or states for queue intake.",
        ),
        _readiness_check(
            key="auto_mode_limits",
            label="Auto-Mode limits",
            ready=auto_mode_ready,
            section="delivery-policy",
            message="Auto-Mode is enabled and has run budget."
            if auto_mode_ready
            else "Enable the project, disable kill switch, and configure positive run limits.",
        ),
        _readiness_check(
            key="qa_gates",
            label="QA gates",
            ready=qa_ready,
            section="delivery-policy",
            message="Blocking QA gates are configured."
            if qa_ready
            else "Configure PR QA evidence and blocking gates.",
        ),
        _readiness_check(
            key="merge_policy",
            label="Merge policy",
            ready=merge_ready,
            section="delivery-policy",
            message="Policy-gated Auto-Merge is enabled."
            if merge_ready
            else "Enable policy-gated Auto-Merge and configure merge strategy.",
        ),
    ]
    return {
        "project_id": project_id,
        "ready": all(check["ready"] for check in checks),
        "environment": environment,
        "checks": checks,
    }


async def _require_delivery_project_member(
    project_id: str,
    session: dict[str, Any],
) -> dict[str, Any]:
    project = await project_registry.get_delivery_project(project_id)
    if project is None:
        raise HTTPException(404, "delivery project not found")
    if _session_is_admin(session):
        return project
    login = str(session.get("sub") or "").strip().lower()
    if login and login in _project_member_logins(project):
        return project
    raise HTTPException(403, "project access required")


async def _filter_repo_records_for_user(
    login: str,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for record in records:
        full_name = record.get("full_name")
        if not isinstance(full_name, str):
            continue
        try:
            await require_repo_access_for_user(login, full_name)
        except HTTPException as exc:
            if exc.status_code in {403, 404}:
                continue
            raise
        out.append(record)
    return out


def _api_base_url() -> str:
    v = os.environ.get("DASHBOARD_API_BASE_URL", "").rstrip("/")
    if not v:
        raise HTTPException(500, "DASHBOARD_API_BASE_URL not configured")
    return v


def _frontend_base_url() -> str:
    v = os.environ.get("DASHBOARD_BASE_URL", "").rstrip("/")
    if not v:
        raise HTTPException(500, "DASHBOARD_BASE_URL not configured")
    return v


def _cookie_security() -> tuple[bool, str]:
    """Cookie ``secure``/``samesite`` flags derived from the API scheme.

    Production serves the API over HTTPS and the dashboard is a separate
    (cross-site) origin, so the session cookie must be ``Secure; SameSite=None``.
    Local dev runs over ``http://localhost`` where ``Secure`` cookies are
    rejected and the frontend/API are same-site, so fall back to
    ``SameSite=Lax`` without ``Secure``.
    """
    if os.environ.get("DASHBOARD_API_BASE_URL", "").startswith("https://"):
        return True, "none"
    return False, "lax"


def _set_session_cookie(response: Response, jwt_token: str) -> None:
    secure, samesite = _cookie_security()
    response.set_cookie(
        key=COOKIE_NAME,
        value=jwt_token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/",
    )


def _set_state_cookie(response: Response, nonce: str) -> None:
    # SameSite=Lax so GitHub's top-level redirect back to /auth/callback
    # still presents this cookie; the cookie is single-purpose and lives
    # only for the duration of one OAuth round-trip.
    secure, _ = _cookie_security()
    response.set_cookie(
        key=STATE_COOKIE_NAME,
        value=nonce,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/dashboard/api/auth",
    )


def _clear_state_cookie(response: Response) -> None:
    secure, _ = _cookie_security()
    response.delete_cookie(
        STATE_COOKIE_NAME, path="/dashboard/api/auth", samesite="lax", secure=secure
    )


def _set_slack_state_cookie(response: Response, nonce: str) -> None:
    secure, _ = _cookie_security()
    response.set_cookie(
        key=SLACK_STATE_COOKIE_NAME,
        value=nonce,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/dashboard/api/slack",
    )


def _clear_slack_state_cookie(response: Response) -> None:
    secure, _ = _cookie_security()
    response.delete_cookie(
        SLACK_STATE_COOKIE_NAME, path="/dashboard/api/slack", samesite="lax", secure=secure
    )


def _set_notion_state_cookie(response: Response, nonce: str) -> None:
    secure, _ = _cookie_security()
    response.set_cookie(
        key=NOTION_STATE_COOKIE_NAME,
        value=nonce,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/dashboard/api/notion",
    )


def _clear_notion_state_cookie(response: Response) -> None:
    secure, _ = _cookie_security()
    response.delete_cookie(
        NOTION_STATE_COOKIE_NAME, path="/dashboard/api/notion", samesite="lax", secure=secure
    )


@router.get("/auth/login")
async def auth_login(
    request: Request,
    redirect_to: str | None = None,
) -> RedirectResponse:
    client_id = os.environ.get("GITHUB_APP_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(500, "GITHUB_APP_CLIENT_ID not configured")
    safe_redirect = sanitize_redirect_to(redirect_to) or _frontend_base_url()

    nonce = new_state_nonce()
    state = issue_state(
        redirect_to=safe_redirect,
        nonce_hash=hash_state_nonce(nonce),
    )
    redirect_uri = f"{_api_base_url()}/dashboard/api/auth/callback"
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
    )
    response = RedirectResponse(url, status_code=302)
    _set_state_cookie(response, nonce)
    return response


@router.get("/auth/callback")
async def auth_callback(request: Request, code: str, state: str) -> RedirectResponse:
    state_payload = decode_state(state)
    state_nonce_hash = state_payload.get("nonce_hash")
    cookie_nonce = request.cookies.get(STATE_COOKIE_NAME)
    if (
        not isinstance(state_nonce_hash, str)
        or not cookie_nonce
        or not hmac.compare_digest(hash_state_nonce(cookie_nonce), state_nonce_hash)
    ):
        # Either the cookie went missing (different browser, expired,
        # cookies blocked) or the state was issued for a different session.
        raise HTTPException(400, "oauth state mismatch — please retry login")

    redirect_to = sanitize_redirect_to(state_payload.get("redirect_to")) or _frontend_base_url()

    token_data = await exchange_code(code)
    access_token = token_data.get("access_token")
    if not isinstance(access_token, str):
        raise HTTPException(400, "oauth exchange missing access_token")
    user, email = await fetch_github_user(access_token)
    login = user.get("login")
    if not login:
        raise HTTPException(400, "could not resolve GitHub login")

    await enforce_org_login_gate(login)

    await upsert_access_token_from_github_response(login, email or "", token_data)

    session_jwt = issue_session(login=login, email=email, avatar_url=user.get("avatar_url"))
    response = RedirectResponse(redirect_to, status_code=302)
    _set_session_cookie(response, session_jwt)
    _clear_state_cookie(response)
    return response


@router.post("/auth/password/login")
async def auth_password_login(body: PasswordLoginBody) -> Response:
    account = await authenticate_password(body.email, body.password)
    session_jwt = issue_session(
        login=str(account["login"]),
        email=str(account["email"]),
        avatar_url=None,
        auth_source="password",
    )
    response = Response(status_code=204)
    _set_session_cookie(response, session_jwt)
    return response


@router.post("/auth/password/reset/request")
async def auth_password_reset_request(body: PasswordResetRequestBody) -> dict[str, str]:
    await request_password_reset(body.email)
    return {"status": "accepted"}


@router.post("/auth/password/reset/confirm")
async def auth_password_reset_confirm(body: PasswordResetConfirmBody) -> Response:
    await reset_password(body.token, body.password)
    return Response(status_code=204)


@router.post("/auth/logout")
async def auth_logout() -> Response:
    response = Response(status_code=204)
    secure, samesite = _cookie_security()
    response.delete_cookie(COOKIE_NAME, path="/", samesite=samesite, secure=secure)
    return response


@router.get("/me")
async def me(session: dict[str, Any] = _SESSION_DEP) -> dict[str, Any]:
    return {
        "login": session["sub"],
        "email": session.get("email"),
        "avatar_url": session.get("avatar_url"),
        "auth_source": session.get("auth_source", "github"),
        "is_admin": _session_is_admin(session),
        "slack_oauth_enabled": slack_oauth_configured(),
    }


@router.get("/options")
async def options() -> dict[str, Any]:
    agent_model, agent_effort = await get_team_default_model("agent")
    subagent_model, subagent_effort = await get_team_default_subagent_model("agent")
    return {
        "models": SUPPORTED_MODELS,
        "default_agent_model": agent_model,
        "default_agent_reasoning_effort": agent_effort,
        "default_agent_subagent_model": subagent_model,
        "default_agent_subagent_reasoning_effort": subagent_effort,
    }


@router.get("/profile")
async def get_my_profile(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    profile = await get_profile(session["sub"])
    return profile or {}


@router.put("/profile")
async def put_my_profile(
    update: ProfileUpdate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    update.validate_pairing()
    return await upsert_profile(session["sub"], session.get("email") or "", update)


@router.get("/my-mapping")
async def get_my_mapping(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    """Return the logged-in user's own GitHub↔Slack mapping (or empty)."""
    mapping = await get_mapping(session["sub"])
    return mapping or {}


@router.get("/my-credentials/currents")
async def get_my_currents_status(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    status = await get_currents_status(session["sub"])
    return status.get("currents", {"connected": False})


@router.put("/my-credentials/currents")
async def connect_my_currents(
    update: CurrentsCredentialsUpdate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    status = await connect_currents(session["sub"], update)
    return status.get("currents", {"connected": False})


@router.delete("/my-credentials/currents")
async def disconnect_my_currents(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    status = await disconnect_currents(session["sub"])
    return status.get("currents", {"connected": False})


@router.get("/my-credentials/notion")
async def get_my_notion_status(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    status = await get_notion_status(session["sub"])
    return status.get("notion", {"connected": False})


@router.delete("/my-credentials/notion")
async def disconnect_my_notion(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    status = await disconnect_notion(session["sub"])
    return status.get("notion", {"connected": False})


@router.get("/my-provider-tokens")
async def api_list_my_provider_tokens(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, list[dict[str, Any]]]:
    return {"items": await list_provider_pat_status(session["sub"])}


@router.get("/my-provider-tokens/{provider}")
async def api_get_my_provider_token(
    provider: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await get_provider_pat_status(session["sub"], provider=provider)


@router.put("/my-provider-tokens/{provider}")
async def api_put_my_provider_token(
    provider: str,
    body: ProviderPATUpdateBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await upsert_provider_pat(session["sub"], provider=provider, token=body.token)


@router.delete("/my-provider-tokens/{provider}")
async def api_delete_my_provider_token(
    provider: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await revoke_provider_pat(session["sub"], provider=provider)


@router.get("/delivery-projects")
async def api_list_delivery_projects(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, list[dict[str, Any]]]:
    projects = await project_registry.list_delivery_projects()
    readable_projects = [
        project for project in projects if _can_read_delivery_project(project, session)
    ]
    latest_runs_by_project: dict[str, list[dict[str, Any]]] = {}
    for project in readable_projects:
        project_id = project.get("project_id")
        if isinstance(project_id, str) and project_id:
            latest_runs_by_project[project_id] = (
                await delivery_queue.list_delivery_queue_items({"project_id": project_id})
            )[:5]
    return {
        "items": [
            _delivery_project_summary(
                project,
                latest_runs=latest_runs_by_project.get(str(project.get("project_id")), []),
            )
            for project in readable_projects
        ]
    }


@router.get("/delivery-projects/{project_id}/readiness")
async def api_get_delivery_project_readiness(
    project_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    project = await _require_delivery_project_member(project_id, session)
    return await _delivery_project_readiness(project, session)


@router.get("/delivery-projects/{project_id}/repositories")
async def api_get_delivery_project_repositories(
    project_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    project = await _require_delivery_project_member(project_id, session)
    return _repository_settings_payload(
        project,
        access_statuses=await _repository_access_statuses(project, session),
    )


@router.put("/delivery-projects/{project_id}/repositories")
async def api_put_delivery_project_repositories(
    project_id: str,
    body: RepositorySettingsUpdateBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    project = await _require_delivery_project_member(project_id, session)
    try:
        payload = _repository_settings_update_payload(project, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    updated = await project_registry.upsert_delivery_project(payload)
    return _repository_settings_payload(
        updated,
        access_statuses=await _repository_access_statuses(updated, session),
    )


@router.post("/delivery-projects/{project_id}/repositories/test-access")
async def api_test_delivery_project_repositories(
    project_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    project = await _require_delivery_project_member(project_id, session)
    return _repository_settings_payload(
        project,
        access_statuses=await _repository_access_statuses(project, session),
    )


@router.get("/delivery-projects/{project_id}/delivery-policy")
async def api_get_delivery_project_policy(
    project_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    project = await _require_delivery_project_member(project_id, session)
    return _delivery_policy_payload(project)


@router.put("/delivery-projects/{project_id}/delivery-policy")
async def api_put_delivery_project_policy(
    project_id: str,
    body: DeliveryPolicyUpdateBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    project = await _require_delivery_project_member(project_id, session)
    updated = await project_registry.upsert_delivery_project(
        _delivery_policy_update_payload(project, body)
    )
    return _delivery_policy_payload(updated)


@router.get("/delivery-projects/{project_id}/model-endpoints/presets")
async def api_list_model_endpoint_presets(
    project_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await _require_delivery_project_member(project_id, session)
    return {"items": project_model_endpoints.endpoint_presets()}


@router.get("/delivery-projects/{project_id}/model-endpoints")
async def api_list_model_endpoints(
    project_id: str,
    environment: str = project_secrets.DEFAULT_AI_HUB_ENVIRONMENT,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await _require_delivery_project_member(project_id, session)
    return await project_model_endpoints.list_model_endpoints(
        project_id,
        environment=environment,
    )


@router.post("/delivery-projects/{project_id}/model-endpoints/presets")
async def api_create_model_endpoint_preset(
    project_id: str,
    body: ModelEndpointPresetCreateBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await _require_delivery_project_member(project_id, session)
    preset = project_model_endpoints.endpoint_preset(body.provider_type)
    return await project_model_endpoints.upsert_model_endpoint(
        project_id,
        environment=body.environment,
        payload=preset,
    )


@router.put("/delivery-projects/{project_id}/model-endpoints/{endpoint_id}")
async def api_put_model_endpoint(
    project_id: str,
    endpoint_id: str,
    body: ModelEndpointUpdateBody,
    environment: str = project_secrets.DEFAULT_AI_HUB_ENVIRONMENT,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await _require_delivery_project_member(project_id, session)
    return await project_model_endpoints.upsert_model_endpoint(
        project_id,
        environment=environment,
        payload={**body.model_dump(), "id": endpoint_id},
    )


@router.post("/delivery-projects/{project_id}/model-endpoints/{endpoint_id}/validate")
async def api_validate_model_endpoint(
    project_id: str,
    endpoint_id: str,
    environment: str = project_secrets.DEFAULT_AI_HUB_ENVIRONMENT,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await _require_delivery_project_member(project_id, session)
    return await project_model_endpoints.validate_model_endpoint(
        project_id,
        environment=environment,
        endpoint_id=endpoint_id,
    )


@router.delete("/delivery-projects/{project_id}/model-endpoints/{endpoint_id}")
async def api_delete_model_endpoint(
    project_id: str,
    endpoint_id: str,
    environment: str = project_secrets.DEFAULT_AI_HUB_ENVIRONMENT,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await _require_delivery_project_member(project_id, session)
    return await project_model_endpoints.delete_model_endpoint(
        project_id,
        environment=environment,
        endpoint_id=endpoint_id,
    )


@router.get("/delivery-projects/{project_id}/model-routing")
async def api_get_model_routing(
    project_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    project = await _require_delivery_project_member(project_id, session)
    payload = project_model_routing.model_routing_payload(project)
    validation = await project_model_routing.validate_project_model_routing_ready(project)
    return {**payload, "validation": validation}


@router.put("/delivery-projects/{project_id}/model-routing")
async def api_put_model_routing(
    project_id: str,
    body: ModelRoutingUpdateBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await _require_delivery_project_member(project_id, session)
    try:
        project = await project_model_routing.set_project_model_routing(
            project_id,
            {
                "environment": body.environment,
                "default": body.default,
                "roles": body.roles,
                "fallback": body.fallback,
            },
            actor=str(session.get("sub") or "unknown"),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    payload = project_model_routing.model_routing_payload(project)
    validation = await project_model_routing.validate_project_model_routing_ready(project)
    return {**payload, "validation": validation}


@router.get("/delivery-projects/{project_id}/ticket-intake")
async def api_get_delivery_project_ticket_intake(
    project_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    project = await _require_delivery_project_member(project_id, session)
    return await _ticket_intake_payload(project, session)


@router.put("/delivery-projects/{project_id}/ticket-intake")
async def api_put_delivery_project_ticket_intake(
    project_id: str,
    body: TicketIntakeUpdateBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    project = await _require_delivery_project_member(project_id, session)
    updated = await project_registry.upsert_delivery_project(
        _ticket_intake_update_payload(project, body)
    )
    return await _ticket_intake_payload(updated, session)


@router.post("/delivery-projects/{project_id}/ticket-intake/test-connection")
async def api_test_delivery_project_ticket_intake(
    project_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await _require_delivery_project_member(project_id, session)
    token = await _linear_token_for_session(
        session,
        project_id=project_id,
        action="ticket_intake_test_connection",
    )
    if not token:
        return {
            "status": "missing_credentials",
            "provider": "linear",
            "teams": [],
            "projects": [],
            "error": "Linear provider token is not configured.",
        }
    try:
        return await linear_queue.test_linear_connection(
            client=linear_queue.LinearGraphQLCatalogClient(token=token)
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "provider": "linear",
            "teams": [],
            "projects": [],
            "error": str(exc),
        }


@router.post("/delivery-projects/{project_id}/ticket-intake/preview")
async def api_preview_delivery_project_ticket_intake(
    project_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    project = await _require_delivery_project_member(project_id, session)
    token = await _linear_token_for_session(
        session,
        project_id=project_id,
        action="ticket_intake_preview",
    )
    if not token:
        return {
            "status": "missing_credentials",
            "provider": "linear",
            "counts": {"queued": 0, "not-ready": 0, "blocked": 0, "ignored": 0},
            "items": [],
            "error": "Linear provider token is not configured.",
        }
    try:
        return await linear_queue.preview_linear_delivery_queue(
            linear_queue.linear_policy_from_project(project),
            client=linear_queue.LinearGraphQLIssueClient(token=token),
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "error",
            "provider": "linear",
            "counts": {"queued": 0, "not-ready": 0, "blocked": 0, "ignored": 0},
            "items": [],
            "error": str(exc),
        }


@router.get("/delivery-projects/{project_id}/secrets")
async def api_list_project_secrets(
    project_id: str,
    environment: str = project_secrets.DEFAULT_AI_HUB_ENVIRONMENT,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, list[dict[str, Any]]]:
    await _require_delivery_project_member(project_id, session)
    return {
        "items": await project_secrets.list_project_secrets(
            project_id,
            environment=environment,
        )
    }


@router.put("/delivery-projects/{project_id}/secrets/{name}")
async def api_put_project_secret(
    project_id: str,
    name: str,
    body: ProjectSecretUpdateBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await _require_delivery_project_member(project_id, session)
    return await project_secrets.upsert_project_secret(
        project_id,
        environment=body.environment,
        name=name,
        value=body.value,
        kind=body.kind,
        updated_by=str(session["sub"]),
    )


@router.post("/delivery-projects/{project_id}/secrets/{name}/test")
async def api_test_project_secret(
    project_id: str,
    name: str,
    body: ProjectSecretTestBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await _require_delivery_project_member(project_id, session)
    return await project_secrets.test_project_secret(
        project_id,
        environment=body.environment,
        name=name,
    )


@router.delete("/delivery-projects/{project_id}/secrets/{name}")
async def api_delete_project_secret(
    project_id: str,
    name: str,
    environment: str = project_secrets.DEFAULT_AI_HUB_ENVIRONMENT,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await _require_delivery_project_member(project_id, session)
    return await project_secrets.revoke_project_secret(
        project_id,
        environment=environment,
        name=name,
    )


@router.get("/delivery-projects/{project_id}/ai-hub/readiness")
async def api_get_project_ai_hub_readiness(
    project_id: str,
    environment: str = project_secrets.DEFAULT_AI_HUB_ENVIRONMENT,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await _require_delivery_project_member(project_id, session)
    return await project_secrets.evaluate_ai_hub_readiness(project_id, environment=environment)


@router.get("/delivery-projects/{project_id}/ai-hub/import-shape")
async def api_get_project_ai_hub_import_shape(
    project_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await _require_delivery_project_member(project_id, session)
    return project_secrets.import_ai_hub_shape_from_env()


@router.post("/delivery-projects/{project_id}/ai-hub/import")
async def api_import_project_ai_hub_secrets(
    project_id: str,
    body: AIHubImportBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await _require_delivery_project_member(project_id, session)
    return await project_secrets.import_ai_hub_secrets_from_env(
        project_id,
        environment=body.environment,
        prefixes=body.prefixes,
        updated_by=str(session["sub"]),
    )


@router.get("/notion/login")
async def notion_login(
    session: dict[str, Any] = _SESSION_DEP,
) -> RedirectResponse:
    redirect_uri = f"{_api_base_url()}/dashboard/api/notion/callback"
    nonce = new_state_nonce()
    nonce_hash = hash_state_nonce(nonce)
    state = issue_state(
        redirect_to=f"{_frontend_base_url()}/my-settings",
        nonce_hash=nonce_hash,
    )
    try:
        url = await store_notion_oauth_flow(
            session["sub"],
            nonce_hash,
            redirect_uri=redirect_uri,
            state=state,
        )
    except NotionOAuthError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    response = RedirectResponse(url, status_code=302)
    _set_notion_state_cookie(response, nonce)
    return response


@router.get("/notion/callback")
async def notion_callback(
    request: Request,
    state: str,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    session: dict[str, Any] = _SESSION_DEP,
) -> RedirectResponse:
    state_payload = decode_state(state)
    nonce_hash = state_payload.get("nonce_hash")
    cookie_nonce = request.cookies.get(NOTION_STATE_COOKIE_NAME)
    if (
        not isinstance(nonce_hash, str)
        or not cookie_nonce
        or not hmac.compare_digest(hash_state_nonce(cookie_nonce), nonce_hash)
    ):
        raise HTTPException(400, "oauth state mismatch — please retry")

    flow = await pop_notion_oauth_flow(session["sub"], nonce_hash)
    if flow is None:
        raise HTTPException(400, "oauth flow expired — please retry")
    if error:
        detail = error_description or error
        raise HTTPException(400, f"Notion OAuth failed: {detail}")
    if not code:
        raise HTTPException(400, "Notion OAuth callback missing code")

    try:
        token_data = await exchange_notion_code(code, flow)
        await connect_notion(session["sub"], token_data, flow)
    except NotionOAuthError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    redirect_to = sanitize_redirect_to(state_payload.get("redirect_to")) or _frontend_base_url()
    response = RedirectResponse(redirect_to, status_code=302)
    _clear_notion_state_cookie(response)
    return response


@router.get("/slack/login")
async def slack_login(
    _session: dict[str, Any] = _SESSION_DEP,
) -> RedirectResponse:
    """Start the Sign in with Slack flow to link the current GitHub account."""
    if not slack_oauth_configured():
        raise HTTPException(500, "Slack OAuth is not configured")
    redirect_uri = f"{_api_base_url()}/dashboard/api/slack/callback"
    nonce = new_state_nonce()
    state = issue_state(
        redirect_to=f"{_frontend_base_url()}/my-settings",
        nonce_hash=hash_state_nonce(nonce),
    )
    response = RedirectResponse(
        build_authorize_url(redirect_uri=redirect_uri, state=state), status_code=302
    )
    _set_slack_state_cookie(response, nonce)
    return response


@router.get("/slack/callback")
async def slack_callback(
    request: Request,
    code: str,
    state: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> RedirectResponse:
    """Link the verified Slack identity to the logged-in GitHub user.

    The Slack member id and email come from Slack's verified OIDC claims, so a
    user can only ever link their own Slack account — no self-asserted values.
    """
    state_payload = decode_state(state)
    nonce_hash = state_payload.get("nonce_hash")
    cookie_nonce = request.cookies.get(SLACK_STATE_COOKIE_NAME)
    if (
        not isinstance(nonce_hash, str)
        or not cookie_nonce
        or not hmac.compare_digest(hash_state_nonce(cookie_nonce), nonce_hash)
    ):
        raise HTTPException(400, "oauth state mismatch — please retry")

    redirect_to = sanitize_redirect_to(state_payload.get("redirect_to")) or _frontend_base_url()
    redirect_uri = f"{_api_base_url()}/dashboard/api/slack/callback"

    access_token = await exchange_slack_code(code, redirect_uri)
    identity = await fetch_slack_identity(access_token)
    verify_team(identity)
    if not identity.email or not identity.email_verified:
        raise HTTPException(400, "your Slack account has no verified email to link")

    await upsert_mapping(
        github_login=session["sub"],
        work_email=identity.email,
        slack_user_id=identity.user_id,
        source="slack_oauth",
        status="active",
    )

    response = RedirectResponse(redirect_to, status_code=302)
    _clear_slack_state_cookie(response)
    return response


@router.get("/team-settings")
async def api_get_team_settings(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await get_team_settings()


@router.put("/team-settings")
async def api_put_team_settings(
    update: TeamSettingsUpdate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await upsert_team_settings(update)


@router.get("/team-credentials")
async def api_get_team_credentials(
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await get_team_credentials_status()


@router.put("/team-credentials/datadog")
async def api_connect_datadog(
    update: DatadogCredentialsUpdate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await connect_datadog(update)


@router.delete("/team-credentials/datadog")
async def api_disconnect_datadog(
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await disconnect_datadog()


@router.put("/team-credentials/langsmith")
async def api_connect_langsmith(
    update: LangSmithCredentialsUpdate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await connect_langsmith(update)


@router.delete("/team-credentials/langsmith")
async def api_disconnect_langsmith(
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await disconnect_langsmith()


class EnabledReviewRepoUpdate(BaseModel):
    full_name: str
    enabled: bool


@router.get("/enabled-review-repos")
async def api_list_enabled_review_repos(
    _session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, list[str]]:
    return {"repos": await list_enabled_review_repos()}


@router.put("/enabled-review-repos")
async def api_set_enabled_review_repo(
    update: EnabledReviewRepoUpdate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, list[str]]:
    repos = await set_review_repo_enabled(update.full_name, update.enabled)
    return {"repos": repos}


@router.get("/repo-snapshots")
async def api_list_repo_snapshots(
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> list[dict[str, Any]]:
    return await list_repo_snapshots()


@router.get("/repo-snapshots/template")
async def api_repo_snapshot_template(
    full_name: str,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, str]:
    try:
        return {"dockerfile": generate_dockerfile_template(normalize_repo_full_name(full_name))}
    except RepoSnapshotConfigError as e:
        raise HTTPException(500, str(e)) from e


@router.post("/repo-snapshots")
async def api_create_repo_snapshot(
    body: RepoSnapshotCreate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    try:
        return await create_repo_snapshot(body.full_name, _admin["sub"])
    except RepoSnapshotConfigError as e:
        raise HTTPException(500, str(e)) from e


@router.get("/repo-snapshots/{full_name:path}")
async def api_get_repo_snapshot(
    full_name: str,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    record = await get_repo_snapshot(normalize_repo_full_name(full_name))
    if not record:
        raise HTTPException(404, "repo snapshot not found")
    return record


@router.put("/repo-snapshots/{full_name:path}")
async def api_update_repo_snapshot(
    full_name: str,
    body: RepoSnapshotUpdate,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await update_repo_snapshot(normalize_repo_full_name(full_name), body)


@router.post("/repo-snapshots/{full_name:path}/build")
async def api_build_repo_snapshot(
    full_name: str,
    background_tasks: BackgroundTasks,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    record = await get_repo_snapshot(full_name)
    if not record:
        raise HTTPException(404, "repo snapshot not found")
    if not (record.get("dockerfile") or "").strip():
        raise HTTPException(400, "dockerfile is empty")
    if record.get("status") == "building" and not is_repo_snapshot_build_stale(record):
        raise HTTPException(409, "a build is already in progress")
    record = await mark_repo_snapshot_building(full_name)
    background_tasks.add_task(run_snapshot_build, full_name)
    return record


@router.delete("/repo-snapshots/{full_name:path}")
async def api_delete_repo_snapshot(
    full_name: str,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> Response:
    full_name = normalize_repo_full_name(full_name)
    record = await get_repo_snapshot(full_name)
    if not record:
        raise HTTPException(404, "repo snapshot not found")
    await delete_repo_snapshot(full_name)
    return Response(status_code=204)


@router.get("/admin/user-mappings")
async def admin_list_user_mappings(
    page: int = 1,
    page_size: int = 20,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    page = max(page, 1)
    page_size = max(1, min(page_size, 100))
    records = await list_mappings()
    total = len(records)
    start = (page - 1) * page_size
    items = records[start : start + page_size]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.delete("/admin/user-mappings/{github_login}")
async def admin_delete_user_mapping(
    github_login: str,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, bool]:
    deleted = await delete_mapping(github_login)
    return {"deleted": deleted}


@router.post("/admin/password-accounts")
async def admin_create_password_account(
    body: PasswordAccountCreateBody,
    admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await upsert_password_account(
        login=body.login,
        email=body.email,
        password=body.password,
        enabled=body.enabled,
        invited_by=str(admin["sub"]),
    )


@router.put("/admin/password-accounts/{email}/enabled")
async def admin_set_password_account_enabled(
    email: str,
    body: PasswordAccountEnabledBody,
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    return await set_password_account_enabled(email, enabled=body.enabled)


@router.post("/admin/password-accounts/{email}/reset-token")
async def admin_create_password_reset_token(
    email: str,
    admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, str]:
    return await create_password_reset_token(email, requested_by=str(admin["sub"]))


@router.get("/admin/evals/reviewer")
async def admin_get_reviewer_eval(
    _admin: dict[str, Any] = _ADMIN_DEP,
) -> dict[str, Any]:
    """Read-only status for the reviewer eval (triggered from the GitHub Action)."""
    return await get_reviewer_eval_status()


def _next_link_url(link_header: str | None) -> str | None:
    if not link_header:
        return None
    # GitHub Link header is comma-separated: '<url>; rel="next", <url>; rel="last"'
    for part in link_header.split(","):
        segments = [s.strip() for s in part.split(";")]
        if len(segments) >= 2 and 'rel="next"' in segments[1] and segments[0].startswith("<"):
            return segments[0][1:-1]
    return None


def _github_api_http_exception(status_code: int) -> HTTPException:
    if status_code == 401:
        return HTTPException(401, "github token expired, re-login required")
    if status_code == 403:
        return HTTPException(403, "github API forbidden")
    if status_code == 404:
        return HTTPException(404, "github API resource not found")
    return HTTPException(502, f"github API error ({status_code})")


async def _paginate(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    items_key: str | None,
    cap: int = 1000,
) -> list[dict[str, Any]]:
    """Follow ``Link: rel="next"`` until exhausted (or cap reached).

    ``items_key`` is the JSON key holding the list when the endpoint returns
    a wrapper object (e.g. ``/user/installations`` returns
    ``{"total_count": N, "installations": [...]}``). When ``None`` the
    response body itself is treated as the list.
    """
    out: list[dict[str, Any]] = []
    next_url: str | None = url
    first = True
    while next_url and len(out) < cap:
        params = {"per_page": "100"} if first else None
        try:
            r = await client.get(next_url, headers=headers, params=params)
        except httpx.TimeoutException as exc:
            logger.warning("GitHub API timed out while paginating %s", next_url)
            raise HTTPException(503, "github API request timed out") from exc
        except httpx.RequestError as exc:
            logger.warning("GitHub API request failed while paginating %s: %s", next_url, exc)
            raise HTTPException(502, "github API request failed") from exc
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "GitHub API returned %s while paginating %s",
                r.status_code,
                next_url,
            )
            raise _github_api_http_exception(r.status_code) from exc
        body = r.json()
        page = body.get(items_key, []) if items_key else body
        if isinstance(page, list):
            out.extend(page)
        next_url = _next_link_url(r.headers.get("Link"))
        first = False
    return out


async def _fetch_user_installations_and_repos(
    login: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve the installations and repos a user can access via the GitHub App.

    Paginates both ``/user/installations`` and per-installation
    ``/user/installations/{id}/repositories`` so users with multiple
    installations or >30 accessible repos get the complete set. Shared by the
    ``/repos`` endpoint and the reviews access filter.
    """
    token = await get_valid_access_token(login)
    if not token:
        raise HTTPException(401, "github token unavailable, re-login required")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=_GITHUB_API_TIMEOUT) as client:
        try:
            installations = await _paginate(
                client,
                "https://api.github.com/user/installations",
                headers=headers,
                items_key="installations",
            )
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
            token = await get_valid_access_token(login, force_refresh=True)
            if not token:
                raise HTTPException(401, "github token expired, re-login required") from exc
            headers["Authorization"] = f"Bearer {token}"
            installations = await _paginate(
                client,
                "https://api.github.com/user/installations",
                headers=headers,
                items_key="installations",
            )
        repositories: list[dict[str, Any]] = []
        for inst in installations:
            inst_id = inst.get("id")
            if inst_id is None:
                continue
            try:
                repos = await _paginate(
                    client,
                    f"https://api.github.com/user/installations/{inst_id}/repositories",
                    headers=headers,
                    items_key="repositories",
                )
            except HTTPException as exc:
                if exc.status_code in _SKIPPABLE_INSTALLATION_REPO_STATUS_CODES:
                    logger.warning(
                        "Skipping installation %s repository list: %s", inst_id, exc.detail
                    )
                    continue
                raise
            repositories.extend(repos)
    return installations, repositories


async def accessible_repo_full_names(login: str) -> frozenset[str]:
    """Lowercased ``owner/name`` of repos the user can currently access.

    Resolved fresh on every call (a fixed, repo-count-independent burst of
    GitHub calls) rather than cached. ``/reviews`` uses this set to decide
    which private PR metadata a user may see, so it's an authorization
    boundary: a stale set would leak repo/PR titles, branches, authors and
    finding counts for repos the user just lost access to.
    """
    _, repositories = await _fetch_user_installations_and_repos(login)
    return frozenset(
        repo["full_name"].lower() for repo in repositories if isinstance(repo.get("full_name"), str)
    )


@router.get("/repos")
async def list_repos(
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    """List repos where open-swe is installed and the user has access."""
    installations, repositories = await _fetch_user_installations_and_repos(session["sub"])
    return {
        "installations": [
            {
                "id": i.get("id"),
                "account": (i.get("account") or {}).get("login"),
                "account_type": (i.get("account") or {}).get("type"),
            }
            for i in installations
        ],
        "repositories": [
            {"full_name": r.get("full_name"), "private": r.get("private", False)}
            for r in repositories
            if r.get("full_name")
        ],
    }


@router.get("/review-styles")
async def api_list_review_styles(
    session: dict[str, Any] = _SESSION_DEP,
) -> list[dict[str, Any]]:
    records = await _filter_repo_records_for_user(session["sub"], await list_review_styles())
    out: list[dict[str, Any]] = []
    for record in records:
        if record.get("status") == "running":
            synced = await sync_review_style_run_status(record["full_name"])
            out.append(synced)
        else:
            out.append(record)
    return out


REVIEWS_PAGE_SIZE = 20


@router.get("/reviews")
async def api_list_reviews(
    page: int = 0,
    mine: bool = True,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    login = session["sub"]
    accessible = await accessible_repo_full_names(login)

    async def is_accessible(summary: dict[str, Any]) -> bool:
        return summary["full_name"].lower() in accessible

    page = max(page, 0)
    reviews, has_more = await list_reviews(
        REVIEWS_PAGE_SIZE,
        offset=page * REVIEWS_PAGE_SIZE,
        author=login if mine else None,
        is_accessible=is_accessible,
    )
    return {"reviews": reviews, "page": page, "has_more": has_more}


@router.get("/reviews/{owner}/{repo}/{pr_number}")
async def api_get_review(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await get_review(owner, repo, pr_number)


@router.get("/reviews/{owner}/{repo}/{pr_number}/diff")
async def api_get_review_diff(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await get_review_diff(owner, repo, pr_number)


@router.get("/reviews/{owner}/{repo}/{pr_number}/image")
async def api_get_review_image(
    owner: str,
    repo: str,
    pr_number: int,
    url: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await proxy_pr_image(owner, repo, pr_number, url)


@router.post("/reviews/{owner}/{repo}/{pr_number}/re-review")
async def api_re_review(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await trigger_re_review(owner, repo, pr_number, session["sub"])


@router.post("/reviews/{owner}/{repo}/{pr_number}/resolve-trace")
async def api_resolve_trace(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await dry_run_trace_resolution(owner, repo, pr_number)


class ReviewCommentCreate(BaseModel):
    path: str
    line: int
    side: Literal["LEFT", "RIGHT"]
    body: str
    start_line: int | None = None
    start_side: Literal["LEFT", "RIGHT"] | None = None


@router.get("/reviews/{owner}/{repo}/{pr_number}/comments")
async def api_list_review_comments(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await list_review_comments(owner, repo, pr_number)


@router.post("/reviews/{owner}/{repo}/{pr_number}/comments")
async def api_create_review_comment(
    owner: str,
    repo: str,
    pr_number: int,
    comment: ReviewCommentCreate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = comment.body.strip()
    if not body:
        raise HTTPException(422, "comment body is required")
    # Post as the signed-in user (their user-to-server token), so the comment is
    # attributed to them rather than the Open SWE app.
    token = await get_valid_access_token(session["sub"])
    if not token:
        raise HTTPException(401, "GitHub re-auth required")
    return await create_review_comment(
        owner,
        repo,
        pr_number,
        token=token,
        path=comment.path,
        line=comment.line,
        side=comment.side,
        body=body,
        start_line=comment.start_line,
        start_side=comment.start_side,
    )


# --- PR chat (sandbox-less ``chat`` graph) -----------------------------------
# The frontend points a LangGraph StreamProvider at the base
# ``/reviews/{owner}/{repo}/{pr_number}/chat``; the SDK then issues the
# ``/threads/{id}/{commands,stream/events,state,history}`` calls proxied below.


@router.get("/reviews/{owner}/{repo}/{pr_number}/chat")
async def api_get_review_chat(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    return await get_review_chat(owner, repo, pr_number, session["sub"])


@router.get("/reviews/{owner}/{repo}/{pr_number}/chat/threads")
async def api_list_review_chat_threads(
    owner: str,
    repo: str,
    pr_number: int,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    threads = await list_review_chat_threads(owner, repo, pr_number, session["sub"])
    return {"threads": threads}


@router.delete("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}")
async def api_delete_review_chat_thread(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    await delete_review_chat_thread(owner, repo, pr_number, session["sub"], thread_id)
    return Response(status_code=204)


@router.post("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/commands")
async def api_review_chat_commands(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    request: Request,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = await request.body()
    status_code, content, media_type = await proxy_review_chat_commands(
        owner,
        repo,
        pr_number,
        session["sub"],
        thread_id,
        body,
        content_type=request.headers.get("content-type", "application/json"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/stream/events")
async def api_review_chat_stream_events(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    request: Request,
    session: dict[str, Any] = _SESSION_DEP,
) -> StreamingResponse:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = await request.body()
    stream = await proxy_review_chat_stream_events(
        owner,
        repo,
        pr_number,
        session["sub"],
        thread_id,
        body,
        content_type=request.headers.get("content-type", "application/json"),
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/state")
async def api_review_chat_state(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    status_code, content, media_type = await proxy_review_chat_state(
        owner, repo, pr_number, session["sub"], thread_id
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/reviews/{owner}/{repo}/{pr_number}/chat/threads/{thread_id}/history")
async def api_review_chat_history(
    owner: str,
    repo: str,
    pr_number: int,
    thread_id: str,
    request: Request,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await require_repo_access_for_user(session["sub"], f"{owner}/{repo}")
    body = await request.body()
    status_code, content, media_type = await proxy_review_chat_history(
        owner,
        repo,
        pr_number,
        session["sub"],
        thread_id,
        body,
        content_type=request.headers.get("content-type", "application/json"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/review-styles")
async def api_create_review_style(
    body: ReviewStyleCreate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], body.full_name)
    return await create_review_style(body.full_name, session["sub"])


@router.get("/review-styles/{full_name:path}")
async def api_get_review_style(
    full_name: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await get_review_style(full_name)
    if not record:
        raise HTTPException(404, "review style not found")
    if record.get("status") == "running":
        record = await sync_review_style_run_status(full_name)
    return record


@router.put("/review-styles/{full_name:path}")
async def api_update_review_style_prompt(
    full_name: str,
    body: ReviewStylePromptUpdate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await get_review_style(full_name)
    if not record:
        raise HTTPException(404, "review style not found")
    return await set_custom_prompt(full_name, body.custom_prompt)


@router.post("/review-styles/{full_name:path}/analyze")
async def api_analyze_review_style(
    full_name: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    token = await require_repo_access_for_user(session["sub"], full_name)
    record = await get_review_style(full_name)
    if not record:
        record = await create_review_style(full_name, session["sub"])
    if record.get("status") == "running":
        record = await sync_review_style_run_status(full_name)
        if record.get("status") == "running":
            raise HTTPException(409, "analysis already running")
    return await start_bootstrap_analysis(
        full_name,
        github_token=token,
        created_by=session["sub"],
    )


@router.post("/review-styles/{full_name:path}/cancel")
async def api_cancel_review_style(
    full_name: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await get_review_style(full_name)
    if not record:
        raise HTTPException(404, "review style not found")
    return await cancel_review_style_analysis(full_name)


@router.delete("/review-styles/{full_name:path}")
async def api_delete_review_style(
    full_name: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await get_review_style(full_name)
    if not record:
        raise HTTPException(404, "review style not found")
    if record.get("status") == "running":
        await cancel_review_style_analysis(full_name)
    await remove_continual_cron(full_name)
    await delete_review_style(full_name)
    return Response(status_code=204)


@router.get("/agent-instructions")
async def api_list_agent_instructions(
    session: dict[str, Any] = _SESSION_DEP,
) -> list[dict[str, Any]]:
    return await _filter_repo_records_for_user(session["sub"], await list_agent_instructions())


@router.post("/agent-instructions")
async def api_create_agent_instructions(
    body: AgentInstructionsCreate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    await require_repo_access_for_user(session["sub"], body.full_name)
    return await create_agent_instructions(body.full_name, session["sub"])


@router.get("/agent-instructions/{full_name:path}")
async def api_get_agent_instructions(
    full_name: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await get_agent_instructions(full_name)
    if not record:
        raise HTTPException(404, "agent instructions not found")
    return record


@router.put("/agent-instructions/{full_name:path}")
async def api_update_agent_instructions(
    full_name: str,
    body: AgentInstructionsUpdate,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    return await set_agent_instructions(full_name, body.instructions)


@router.delete("/agent-instructions/{full_name:path}")
async def api_delete_agent_instructions(
    full_name: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    full_name = normalize_repo_full_name(full_name)
    await require_repo_access_for_user(session["sub"], full_name)
    record = await get_agent_instructions(full_name)
    if not record:
        raise HTTPException(404, "agent instructions not found")
    await delete_agent_instructions(full_name)
    return Response(status_code=204)


@router.get("/agent-usage-leaderboard")
async def api_agent_usage_leaderboard(
    background_tasks: BackgroundTasks,
    period: str | None = "30d",
    limit: int = 10,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await list_agent_usage_leaderboard(
        period=period,
        limit=limit,
        current_login=session["sub"],
        current_email=session.get("email"),
        schedule_usage_refresh=lambda cache_period: background_tasks.add_task(
            refresh_usage_leaderboard_cache, cache_period
        ),
        schedule_reviewer_refresh=lambda cache_period: background_tasks.add_task(
            refresh_reviewer_stats_cache, cache_period
        ),
    )


@router.get("/schedules")
async def api_list_schedules(
    session: dict[str, Any] = _SESSION_DEP,
) -> list[dict[str, Any]]:
    return await list_agent_schedules(session["sub"], email=session.get("email"))


@router.post("/schedules")
async def api_create_schedule(
    body: ScheduleCreateBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await create_agent_schedule(session["sub"], body, email=session.get("email"))


@router.patch("/schedules/{schedule_id}")
async def api_update_schedule(
    schedule_id: str,
    body: ScheduleUpdateBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await update_agent_schedule(
        schedule_id, session["sub"], body, email=session.get("email")
    )


@router.delete("/schedules/{schedule_id}")
async def api_delete_schedule(
    schedule_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await delete_agent_schedule(schedule_id, session["sub"], email=session.get("email"))
    return Response(status_code=204)


@router.get("/threads")
async def api_list_threads(
    all: bool = False,
    session: dict[str, Any] = _SESSION_DEP,
) -> list[dict[str, Any]]:
    if all and not _session_is_admin(session):
        raise HTTPException(403, "admin only")
    return await list_dashboard_threads(session["sub"], email=session.get("email"), include_all=all)


@router.get("/threads/sidebar")
async def api_list_threads_sidebar(
    active_limit: int = 50,
    resolved_limit: int = 20,
    all: bool = False,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    if all and not _session_is_admin(session):
        raise HTTPException(403, "admin only")
    return await list_dashboard_threads_sidebar(
        session["sub"],
        email=session.get("email"),
        active_limit=active_limit,
        resolved_limit=resolved_limit,
        include_all=all,
    )


@router.get("/threads/page")
async def api_list_threads_page(
    limit: int = 25,
    offset: int = 0,
    all: bool = False,
    resolved: bool | None = None,
    viewed: bool | None = None,
    source: str | None = None,
    status: str | None = None,
    q: str | None = None,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    if all and not _session_is_admin(session):
        raise HTTPException(403, "admin only")
    return await list_dashboard_threads_page(
        session["sub"],
        email=session.get("email"),
        limit=limit,
        offset=offset,
        include_all=all,
        resolved=resolved,
        viewed=viewed,
        source=source,
        status=status,
        query=q,
    )


@router.get("/threads/{thread_id}")
async def api_get_thread(
    thread_id: str,
    mark_viewed: bool = True,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await get_dashboard_thread(
        thread_id,
        session["sub"],
        email=session.get("email"),
        mark_viewed=mark_viewed,
    )


@router.get("/threads/{thread_id}/recovery.patch")
async def api_get_thread_recovery_patch(
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    content, filename = await get_dashboard_thread_recovery_patch(
        thread_id,
        session["sub"],
        email=session.get("email"),
    )
    return Response(
        content=content,
        media_type="text/x-diff",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/threads/{thread_id}/pr-diff")
async def api_get_thread_pr_diff(
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await get_dashboard_thread_pr_diff(
        thread_id,
        session["sub"],
        email=session.get("email"),
    )


@router.post("/threads/{thread_id}/messages")
async def api_send_thread_message(
    thread_id: str,
    body: ThreadMessageBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await send_dashboard_message(thread_id, session["sub"], body, email=session.get("email"))


@router.post("/threads/{thread_id}/resolve")
async def api_resolve_thread(
    thread_id: str,
    body: ThreadResolveBody,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await resolve_dashboard_thread(
        thread_id,
        session["sub"],
        resolved=body.resolved,
        email=session.get("email"),
    )


@router.post("/threads/{thread_id}/runs/{run_id}/cancel")
async def api_cancel_thread_run(
    thread_id: str,
    run_id: str,
    session: dict[str, Any] = _SESSION_DEP,
    wait: str = "0",
    action: str = "interrupt",
) -> Response:
    status_code, content, media_type = await proxy_dashboard_thread_run_cancel(
        thread_id,
        run_id,
        session["sub"],
        wait=wait,
        action=action,
        email=session.get("email"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/threads/{thread_id}/cancel")
async def api_cancel_thread(
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await cancel_dashboard_thread(thread_id, session["sub"], email=session.get("email"))


@router.delete("/threads/{thread_id}")
async def api_delete_thread(
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    await delete_dashboard_thread(thread_id, session["sub"], email=session.get("email"))
    return Response(status_code=204)


@router.get("/threads/{thread_id}/state")
async def api_get_thread_state(
    thread_id: str,
    session: dict[str, Any] = _SESSION_DEP,
) -> dict[str, Any]:
    return await get_dashboard_thread_state(thread_id, session["sub"], email=session.get("email"))


@router.post("/threads/{thread_id}/stream/events")
async def api_thread_stream_events(
    thread_id: str,
    request: Request,
    session: dict[str, Any] = _SESSION_DEP,
) -> StreamingResponse:
    body = await request.body()
    stream = await proxy_dashboard_thread_stream_events(
        thread_id,
        session["sub"],
        body,
        email=session.get("email"),
        content_type=request.headers.get("content-type", "application/json"),
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/threads/{thread_id}/commands")
async def api_thread_commands(
    thread_id: str,
    request: Request,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    body = await request.body()
    status_code, content, media_type = await proxy_dashboard_thread_commands(
        thread_id,
        session["sub"],
        body,
        email=session.get("email"),
        content_type=request.headers.get("content-type", "application/json"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.post("/threads/{thread_id}/history")
async def api_thread_history(
    thread_id: str,
    request: Request,
    session: dict[str, Any] = _SESSION_DEP,
) -> Response:
    body = await request.body()
    status_code, content, media_type = await proxy_dashboard_thread_history(
        thread_id,
        session["sub"],
        body,
        email=session.get("email"),
        content_type=request.headers.get("content-type", "application/json"),
    )
    return Response(content=content, status_code=status_code, media_type=media_type)


@router.get("/threads/{thread_id}/stream")
async def api_stream_thread(
    thread_id: str,
    request: Request,
    session: dict[str, Any] = _SESSION_DEP,
) -> StreamingResponse:
    last_event_id = request.headers.get("last-event-id")

    async def event_generator():
        async for chunk in stream_dashboard_thread(
            thread_id, session["sub"], email=session.get("email"), last_event_id=last_event_id
        ):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
