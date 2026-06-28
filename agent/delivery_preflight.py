"""Delivery start preflight and Auto-Mode limits."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .delivery_queue import PreflightBlocker, PreflightResult, transition_delivery_queue_status

AUTO_MODE_BLOCKER_MESSAGES: dict[str, str] = {
    "auto_active_run_limit": "Project already has the maximum active Auto-Run count.",
    "auto_start_queue_limit": "Project already has the maximum auto-startable queue size.",
    "run_retry_limit": "Run retry limit is exhausted.",
    "gate_retry_limit": "Gate retry limit is exhausted.",
    "auto_rework_limit": "Auto-Rework pass limit is exhausted.",
    "daily_budget": "Daily Auto-Mode budget is exhausted.",
}

_START_BLOCKER_MESSAGES: dict[str, str] = {
    "active_project": "Project is not active.",
    "readiness": "Work item is not ready for delivery.",
    "issue_context": "Linear issue context is missing.",
    "credentials": "GitHub credentials are unavailable.",
    "ai_hub_ready": "AI Hub is not ready.",
    "sandbox_profile": "Sandbox profile is unavailable.",
    "budget": "Delivery budget is unavailable.",
    "duplicate_active_run": "Another active run already exists for this work item.",
    "kill_switch": "Delivery queue kill switch is enabled.",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_mapping(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(value)


def _bool_from_checks(
    checks: Mapping[str, Any],
    key: str,
    default: bool,
) -> bool:
    value = checks.get(key)
    return value if isinstance(value, bool) else default


def _limit(project: Mapping[str, Any], key: str, default: int) -> int:
    run_limits = _mapping(project.get("run_limits"))
    value = run_limits.get(key)
    return value if isinstance(value, int) and value >= 0 else default


def _blockers(
    failing_checks: Mapping[str, bool], messages: Mapping[str, str]
) -> list[PreflightBlocker]:
    return [
        {"code": code, "message": messages[code]}
        for code, blocked in failing_checks.items()
        if blocked
    ]


def evaluate_auto_mode_limits(
    project: Mapping[str, Any],
    *,
    active_auto_runs: int = 0,
    auto_startable_items: int = 0,
    run_retries: int = 0,
    gate_retries: int = 0,
    auto_rework_passes: int = 0,
    daily_budget_remaining: int | None = None,
) -> PreflightResult:
    daily_budget = _limit(project, "daily_run_budget", 10)
    if daily_budget_remaining is None:
        daily_budget_remaining = daily_budget
    blockers = _blockers(
        {
            "auto_active_run_limit": active_auto_runs
            >= _limit(project, "max_concurrent_auto_runs", 1),
            "auto_start_queue_limit": auto_startable_items
            >= _limit(project, "max_auto_startable_items", 5),
            "run_retry_limit": run_retries >= _limit(project, "max_run_retries", 1),
            "gate_retry_limit": gate_retries >= _limit(project, "max_gate_retries", 1),
            "auto_rework_limit": auto_rework_passes >= _limit(project, "max_auto_rework_passes", 1),
            "daily_budget": daily_budget_remaining <= 0,
        },
        AUTO_MODE_BLOCKER_MESSAGES,
    )
    return {"ready": not blockers, "blockers": blockers}


def evaluate_delivery_start_preflight(
    item: Mapping[str, Any],
    project: Mapping[str, Any],
    *,
    checks: Mapping[str, Any] | None = None,
    auto_mode: PreflightResult | None = None,
) -> PreflightResult:
    checks = checks or {}
    item_preflight = _mapping(item.get("preflight"))
    item_preflight_ready = item_preflight.get("ready")
    readiness = item.get("status") == "queued" and item_preflight_ready is not False
    issue_context = _has_text(item.get("description")) and not item.get("missing_required_fields")
    credentials = _bool_from_checks(
        checks,
        "github_credentials",
        _has_text(item.get("credential_identity")) or _has_text(item.get("github_login")),
    )
    ai_hub_ready = _bool_from_checks(checks, "ai_hub_ready", item.get("model_snapshot") is not None)
    sandbox_profile = _bool_from_checks(
        checks,
        "sandbox_profile",
        _has_mapping(item.get("sandbox_profile")) or _has_mapping(project.get("sandbox_profile")),
    )
    blockers = _blockers(
        {
            "active_project": not bool(project.get("active", True)),
            "readiness": not readiness,
            "issue_context": not issue_context,
            "credentials": not credentials,
            "ai_hub_ready": not ai_hub_ready,
            "sandbox_profile": not sandbox_profile,
            "budget": not _bool_from_checks(checks, "budget_available", True),
            "duplicate_active_run": bool(checks.get("duplicate_active_run", False)),
            "kill_switch": bool(project.get("kill_switch", False)),
        },
        _START_BLOCKER_MESSAGES,
    )
    if auto_mode and not auto_mode["ready"]:
        blockers.extend(auto_mode["blockers"])
    return {"ready": not blockers, "blockers": blockers}


async def block_delivery_start(item_id: str, result: PreflightResult) -> dict[str, Any]:
    return await transition_delivery_queue_status(
        item_id,
        "blocked",
        reason="start_preflight_failed",
        extra={"preflight": result, "blockers": result["blockers"]},
    )
