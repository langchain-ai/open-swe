"""Store-backed delivery project registry."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, TypedDict

from .utils.thread_ops import langgraph_client

DELIVERY_PROJECTS_NAMESPACE: list[str] = ["delivery_projects"]


class ProjectStartBlocker(TypedDict):
    code: str
    message: str


class ProjectStartPolicyResult(TypedDict):
    ready: bool
    blockers: list[ProjectStartBlocker]


_BLOCKER_MESSAGES: dict[str, str] = {
    "disabled_project": "Project is disabled.",
    "kill_switch": "Project kill switch is enabled.",
    "missing_tracker_config": "Tracker configuration is missing.",
    "missing_vcs_config": "Version-control configuration is missing.",
    "missing_sandbox": "Sandbox profile is missing.",
    "missing_budget": "Delivery budget is unavailable.",
}
_DEFAULT_QUEUE_ELIGIBILITY_POLICY: dict[str, Any] = {
    "ready_states": ["ready"],
    "labels": ["agent-ready"],
}
_DEFAULT_SANDBOX_PROFILE: dict[str, Any] = {
    "provider": "langsmith",
    "profile": "default",
}
_DEFAULT_BRANCH_POLICY: dict[str, Any] = {
    "base_branch": "main",
    "branch_prefix": "delivery",
    "draft_pull_requests": True,
}
_DEFAULT_GATE_POLICY: dict[str, Any] = {
    "agent_review": True,
    "qa_evidence": True,
    "required_evidence": ["tests"],
}
_DEFAULT_CONTEXT_PACK: dict[str, Any] = {
    "documents": [],
    "repositories": [],
}
_DEFAULT_CREDENTIAL_POLICY: dict[str, Any] = {
    "provider": "github",
    "scope": "user",
    "requires_user_pat": True,
}
_DEFAULT_MERGE_POLICY: dict[str, Any] = {
    "enabled": False,
    "strategy": "squash",
    "required_checks": [],
    "delete_branch": True,
}
_DEFAULT_RUN_LIMITS: dict[str, Any] = {
    "max_concurrent_runs": 1,
    "daily_run_budget": 10,
}
_DEFAULT_MEMBERSHIP: dict[str, Any] = {
    "users": [],
}


def _client():
    return langgraph_client()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _value_from_item(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _mapping_value(value: Any, key: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a mapping")
    return deepcopy(dict(value))


def _bool_value(value: Any, key: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _provider_record(
    *,
    provider: str,
    config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    provider = provider.strip().lower()
    if not provider:
        raise ValueError("provider is required")
    return {"provider": provider, "config": deepcopy(dict(config or {}))}


def _has_provider_config(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    provider = value.get("provider")
    config = value.get("config")
    return (
        bool(isinstance(provider, str) and provider.strip())
        and isinstance(config, Mapping)
        and bool(config)
    )


def _has_sandbox_profile(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(value)


def default_delivery_project(
    *,
    project_id: str,
    name: str,
    tracker_provider: str = "linear",
    tracker_config: Mapping[str, Any] | None = None,
    vcs_provider: str = "github",
    vcs_config: Mapping[str, Any] | None = None,
    queue_eligibility_policy: Mapping[str, Any] | None = None,
    sandbox_profile: Mapping[str, Any] | None = None,
    branch_policy: Mapping[str, Any] | None = None,
    gate_policy: Mapping[str, Any] | None = None,
    context_pack: Mapping[str, Any] | None = None,
    credential_policy: Mapping[str, Any] | None = None,
    merge_policy: Mapping[str, Any] | None = None,
    delivery_modes: list[str] | None = None,
    run_limits: Mapping[str, Any] | None = None,
    membership: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "project_id": project_id.strip(),
        "name": name.strip(),
        "active": True,
        "kill_switch": False,
        "tracker": _provider_record(provider=tracker_provider, config=tracker_config),
        "vcs": _provider_record(provider=vcs_provider, config=vcs_config),
        "queue_eligibility_policy": deepcopy(
            dict(queue_eligibility_policy or _DEFAULT_QUEUE_ELIGIBILITY_POLICY)
        ),
        "sandbox_profile": deepcopy(dict(sandbox_profile or _DEFAULT_SANDBOX_PROFILE)),
        "branch_policy": deepcopy(dict(branch_policy or _DEFAULT_BRANCH_POLICY)),
        "gate_policy": deepcopy(dict(gate_policy or _DEFAULT_GATE_POLICY)),
        "context_pack": deepcopy(dict(context_pack or _DEFAULT_CONTEXT_PACK)),
        "credential_policy": deepcopy(dict(credential_policy or _DEFAULT_CREDENTIAL_POLICY)),
        "merge_policy": deepcopy(dict(merge_policy or _DEFAULT_MERGE_POLICY)),
        "delivery_modes": list(delivery_modes or ["queued_delivery"]),
        "run_limits": deepcopy(dict(run_limits or _DEFAULT_RUN_LIMITS)),
        "membership": deepcopy(dict(membership or _DEFAULT_MEMBERSHIP)),
    }


def default_sports_cms_delivery_project(
    *,
    tracker_config: Mapping[str, Any],
    vcs_config: Mapping[str, Any],
    membership: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return default_delivery_project(
        project_id="sports-cms",
        name="Sports CMS",
        tracker_provider="linear",
        tracker_config=tracker_config,
        vcs_provider="github",
        vcs_config=vcs_config,
        queue_eligibility_policy={
            "ready_states": ["ready"],
            "labels": ["agent-ready"],
            "missing_readiness": "not-ready",
            "excluded_statuses": ["done", "completed", "canceled", "cancelled", "duplicate"],
            "required_fields": ["description"],
        },
        sandbox_profile={"provider": "langsmith", "profile": "sports-cms"},
        branch_policy={
            "base_branch": "main",
            "branch_prefix": "delivery/sports-cms",
            "draft_pull_requests": True,
        },
        gate_policy={
            "agent_review": True,
            "qa_evidence": True,
            "blocking_gates": [
                "drupal_bootstrap",
                "theme_assets",
                "sdc_twig_render",
                "browser_flow",
                "screenshot",
                "trace_or_video",
                "pr_qa_evidence",
            ],
            "advisory_gates": ["phpcs", "phpstan", "phpunit"],
        },
        context_pack={
            "domains": ["drupal", "sdc", "frontend"],
            "required_context": ["project_readme", "theme_components", "qa_gates"],
        },
        credential_policy={
            "provider": "github",
            "scope": "user",
            "requires_user_pat": True,
            "allowed_actions": ["branch", "commit", "pull_request"],
        },
        membership=membership,
    )


def evaluate_project_start_policy(
    project: Mapping[str, Any],
    *,
    budget_available: bool = True,
) -> ProjectStartPolicyResult:
    failing_checks = {
        "disabled_project": not bool(project.get("active", True)),
        "kill_switch": bool(project.get("kill_switch", False)),
        "missing_tracker_config": not _has_provider_config(project.get("tracker")),
        "missing_vcs_config": not _has_provider_config(project.get("vcs")),
        "missing_sandbox": not _has_sandbox_profile(project.get("sandbox_profile")),
        "missing_budget": not budget_available,
    }
    blockers = [
        {"code": code, "message": _BLOCKER_MESSAGES[code]}
        for code, blocked in failing_checks.items()
        if blocked
    ]
    return {"ready": not blockers, "blockers": blockers}


async def get_delivery_project(project_id: str) -> dict[str, Any] | None:
    item = await _client().store.get_item(DELIVERY_PROJECTS_NAMESPACE, project_id.strip())
    value = _value_from_item(item)
    return deepcopy(value) if value is not None else None


async def list_delivery_projects(filter: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    limit = 100
    offset = 0
    while True:
        result = await _client().store.search_items(
            DELIVERY_PROJECTS_NAMESPACE,
            filter=filter,
            limit=limit,
            offset=offset,
        )
        items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
        if not items:
            break
        for item in items:
            value = _value_from_item(item)
            if value is not None:
                records.append(deepcopy(value))
        if len(items) < limit:
            break
        offset += len(items)
    records.sort(key=lambda item: item.get("project_id", ""))
    return records


def _connection_from_payload(
    payload: Mapping[str, Any],
    record: Mapping[str, Any],
    field: str,
) -> dict[str, Any]:
    connection = payload.get(field)
    if connection is None:
        connection = record.get(field)
    if not isinstance(connection, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return _provider_record(
        provider=_required_text(connection, "provider"),
        config=connection.get("config") if isinstance(connection.get("config"), Mapping) else {},
    )


def _apply_project_update(record: dict[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    if "name" in payload:
        record["name"] = _required_text(payload, "name")
    for key in ("active", "kill_switch"):
        if key in payload:
            record[key] = _bool_value(payload[key], key)
    for key in (
        "queue_eligibility_policy",
        "branch_policy",
        "gate_policy",
        "context_pack",
        "credential_policy",
        "merge_policy",
        "run_limits",
        "membership",
    ):
        if key in payload:
            record[key] = _mapping_value(payload[key], key)
    if "sandbox_profile" in payload:
        sandbox_profile = payload["sandbox_profile"]
        record["sandbox_profile"] = (
            None if sandbox_profile is None else _mapping_value(sandbox_profile, "sandbox_profile")
        )
    if "delivery_modes" in payload:
        modes = payload["delivery_modes"]
        if not isinstance(modes, list) or not all(isinstance(mode, str) for mode in modes):
            raise ValueError("delivery_modes must be a list of strings")
        record["delivery_modes"] = list(modes)
    if "tracker" in payload:
        record["tracker"] = _connection_from_payload(payload, record, "tracker")
    if "vcs" in payload:
        record["vcs"] = _connection_from_payload(payload, record, "vcs")
    return record


async def upsert_delivery_project(payload: Mapping[str, Any]) -> dict[str, Any]:
    project_id = _required_text(payload, "project_id")
    existing = await get_delivery_project(project_id)
    if existing is None:
        record = default_delivery_project(
            project_id=project_id,
            name=_required_text(payload, "name"),
        )
    else:
        record = deepcopy(existing)

    record["project_id"] = project_id
    record = _apply_project_update(record, payload)
    now = _now_iso()
    stored = {
        **record,
        "created_at": record.get("created_at") or now,
        "updated_at": now,
    }
    await _client().store.put_item(DELIVERY_PROJECTS_NAMESPACE, project_id, stored)
    return deepcopy(stored)


async def get_project_merge_policy(project_id: str) -> dict[str, Any] | None:
    project = await get_delivery_project(project_id)
    if project is None:
        return None
    merge_policy = project.get("merge_policy")
    return deepcopy(dict(merge_policy)) if isinstance(merge_policy, Mapping) else None


async def get_project_branch_policy(project_id: str) -> dict[str, Any] | None:
    project = await get_delivery_project(project_id)
    if project is None:
        return None
    branch_policy = project.get("branch_policy")
    return deepcopy(dict(branch_policy)) if isinstance(branch_policy, Mapping) else None
