"""Per-repository automatic approval policies for Open SWE Review."""

import logging
from datetime import UTC, datetime
from typing import TypedDict

from langgraph_sdk import get_client

from .review_styles import normalize_repo_full_name
from .team_settings import get_team_settings

logger = logging.getLogger(__name__)

REVIEW_APPROVAL_POLICIES_NAMESPACE: list[str] = ["review_approval_policies"]
REVIEW_APPROVAL_POLICIES_KEY = "default"
DEFAULT_AUTO_APPROVE_THRESHOLD = 90


class RepoApprovalPolicy(TypedDict):
    full_name: str
    enabled: bool
    threshold: int | None
    updated_at: str | None


class EffectiveApprovalPolicy(TypedDict):
    team_enabled: bool
    team_threshold: int
    repo_enabled: bool
    repo_threshold: int | None
    effective_enabled: bool
    effective_threshold: int


def _client():
    return get_client()


def _valid_threshold(value: object, *, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if type(value) is not int or not 0 <= value <= 100:
        raise ValueError("threshold must be an integer between 0 and 100")
    return value


def _default_policy(full_name: str) -> RepoApprovalPolicy:
    return {
        "full_name": normalize_repo_full_name(full_name).casefold(),
        "enabled": False,
        "threshold": None,
        "updated_at": None,
    }


def _coerce_policy(full_name: str, value: object) -> RepoApprovalPolicy:
    default = _default_policy(full_name)
    if not isinstance(value, dict):
        return default
    threshold = value.get("threshold")
    if threshold is not None and (type(threshold) is not int or not 0 <= threshold <= 100):
        threshold = None
    return {
        "full_name": default["full_name"],
        "enabled": value.get("enabled") is True,
        "threshold": threshold,
        "updated_at": value.get("updated_at") if isinstance(value.get("updated_at"), str) else None,
    }


async def list_review_approval_policies() -> list[RepoApprovalPolicy]:
    try:
        item = await _client().store.get_item(
            REVIEW_APPROVAL_POLICIES_NAMESPACE, REVIEW_APPROVAL_POLICIES_KEY
        )
    except Exception:
        logger.warning("Review approval policy lookup failed", exc_info=True)
        return []
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    records = value.get("policies") if isinstance(value, dict) else None
    if not isinstance(records, dict):
        return []
    policies = [
        _coerce_policy(name, policy) for name, policy in records.items() if isinstance(name, str)
    ]
    return sorted(policies, key=lambda policy: policy["full_name"])


async def get_review_approval_policy(full_name: str) -> RepoApprovalPolicy:
    normalized = normalize_repo_full_name(full_name).casefold()
    try:
        policies = await list_review_approval_policies()
    except Exception:
        return _default_policy(normalized)
    return next(
        (policy for policy in policies if policy["full_name"].casefold() == normalized.casefold()),
        _default_policy(normalized),
    )


async def set_review_approval_policy(
    full_name: str,
    *,
    enabled: bool,
    threshold: int | None,
) -> RepoApprovalPolicy:
    normalized = normalize_repo_full_name(full_name).casefold()
    validated_threshold = _valid_threshold(threshold, allow_none=True)
    now = datetime.now(UTC).isoformat()
    try:
        item = await _client().store.get_item(
            REVIEW_APPROVAL_POLICIES_NAMESPACE, REVIEW_APPROVAL_POLICIES_KEY
        )
        value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
        raw_policies = value.get("policies") if isinstance(value, dict) else None
        policies = dict(raw_policies) if isinstance(raw_policies, dict) else {}
        policy: RepoApprovalPolicy = {
            "full_name": normalized,
            "enabled": enabled,
            "threshold": validated_threshold,
            "updated_at": now,
        }
        policies[normalized] = policy
        await _client().store.put_item(
            REVIEW_APPROVAL_POLICIES_NAMESPACE,
            REVIEW_APPROVAL_POLICIES_KEY,
            {"policies": policies, "updated_at": now},
        )
        return policy
    except ValueError:
        raise
    except Exception:
        logger.exception("Failed to persist review approval policy for %s", normalized)
        raise


async def get_effective_review_approval_policy(owner: str, repo: str) -> EffectiveApprovalPolicy:
    team = await get_team_settings()
    repo_policy = await get_review_approval_policy(f"{owner}/{repo}")
    team_threshold_raw = team.get("auto_approve_default_threshold")
    team_threshold = (
        team_threshold_raw
        if type(team_threshold_raw) is int and 0 <= team_threshold_raw <= 100
        else DEFAULT_AUTO_APPROVE_THRESHOLD
    )
    repo_threshold = repo_policy["threshold"]
    return {
        "team_enabled": team.get("auto_approve_enabled") is True,
        "team_threshold": team_threshold,
        "repo_enabled": repo_policy["enabled"],
        "repo_threshold": repo_threshold,
        "effective_enabled": team.get("auto_approve_enabled") is True and repo_policy["enabled"],
        "effective_threshold": repo_threshold if repo_threshold is not None else team_threshold,
    }
