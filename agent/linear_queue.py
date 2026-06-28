"""Linear delivery queue polling."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from . import delivery_queue

ReadinessMissingAction = Literal["skip", "not-ready"]


@dataclass(frozen=True)
class LinearQueueFieldMappings:
    priority: str | None = "priority"
    priority_label: str | None = "priorityLabel"
    risk: str | None = None


@dataclass(frozen=True)
class LinearQueueEligibilityPolicy:
    project_id: str
    readiness_label: str = "agent-ready"
    missing_readiness: ReadinessMissingAction = "skip"
    excluded_statuses: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ()
    team_ids: tuple[str, ...] = ()
    team_keys: tuple[str, ...] = ()
    team_names: tuple[str, ...] = ()
    linear_project_ids: tuple[str, ...] = ()
    linear_project_names: tuple[str, ...] = ()
    fields: LinearQueueFieldMappings = field(default_factory=LinearQueueFieldMappings)


class LinearIssueClient(Protocol):
    def list_issues(
        self,
        *,
        readiness_label: str,
        team_ids: Sequence[str],
        team_keys: Sequence[str],
        team_names: Sequence[str],
        linear_project_ids: Sequence[str],
        linear_project_names: Sequence[str],
        excluded_statuses: Sequence[str],
    ) -> Awaitable[Iterable[Mapping[str, Any]]] | Iterable[Mapping[str, Any]]: ...


def _normal_set(values: Sequence[str]) -> set[str]:
    return {value.strip().lower() for value in values if value.strip()}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _field_value(issue: Mapping[str, Any], path: str) -> Any:
    current: Any = issue
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Sequence) and not isinstance(value, str):
        return bool(value)
    return True


def _labels(issue: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    labels = issue.get("labels")
    if isinstance(labels, Mapping):
        nodes = labels.get("nodes") or []
        return [label for label in nodes if isinstance(label, Mapping)]
    if isinstance(labels, Sequence) and not isinstance(labels, str):
        return [label for label in labels if isinstance(label, Mapping)]
    return []


def _has_label(issue: Mapping[str, Any], label_name: str) -> bool:
    expected = label_name.strip().lower()
    return any(str(label.get("name", "")).strip().lower() == expected for label in _labels(issue))


def _matches_any(mapping: Mapping[str, Any], keys: Sequence[str], allowed: set[str]) -> bool:
    if not allowed:
        return True
    return any(str(mapping.get(key, "")).strip().lower() in allowed for key in keys)


def _in_scope(issue: Mapping[str, Any], policy: LinearQueueEligibilityPolicy) -> bool:
    team = _mapping(issue.get("team"))
    project = _mapping(issue.get("project"))
    return (
        _matches_any(team, ("id",), _normal_set(policy.team_ids))
        and _matches_any(team, ("key",), _normal_set(policy.team_keys))
        and _matches_any(team, ("name",), _normal_set(policy.team_names))
        and _matches_any(project, ("id",), _normal_set(policy.linear_project_ids))
        and _matches_any(project, ("name",), _normal_set(policy.linear_project_names))
    )


def _is_excluded_status(issue: Mapping[str, Any], policy: LinearQueueEligibilityPolicy) -> bool:
    excluded = _normal_set(policy.excluded_statuses)
    if not excluded:
        return False
    state = _mapping(issue.get("state"))
    return any(
        str(state.get(key, "")).strip().lower() in excluded for key in ("id", "name", "type")
    )


def _missing_required_fields(
    issue: Mapping[str, Any], policy: LinearQueueEligibilityPolicy
) -> list[str]:
    return [field for field in policy.required_fields if not _has_value(_field_value(issue, field))]


def _ready_preflight(*, readiness: bool, issue_context: bool) -> delivery_queue.PreflightInput:
    return {
        "active_project": True,
        "readiness": readiness,
        "issue_context": issue_context,
        "credentials": True,
        "ai_hub_ready": True,
        "sandbox_profile": True,
        "budget": True,
        "duplicate_active_run": False,
        "kill_switch": False,
    }


def _linear_metadata(
    issue: Mapping[str, Any], policy: LinearQueueEligibilityPolicy
) -> dict[str, Any]:
    priority = _field_value(issue, policy.fields.priority) if policy.fields.priority else None
    priority_label = (
        _field_value(issue, policy.fields.priority_label) if policy.fields.priority_label else None
    )
    risk = _field_value(issue, policy.fields.risk) if policy.fields.risk else None
    metadata = {
        "issue_id": issue.get("id"),
        "identifier": issue.get("identifier"),
        "team": dict(_mapping(issue.get("team"))),
        "project": dict(_mapping(issue.get("project"))),
        "state": dict(_mapping(issue.get("state"))),
        "labels": [dict(label) for label in _labels(issue)],
    }
    if priority is not None:
        metadata["priority"] = priority
    if priority_label is not None:
        metadata["priority_label"] = priority_label
    if risk is not None:
        metadata["risk"] = risk
    return metadata


def _payload_for_issue(
    issue: Mapping[str, Any],
    policy: LinearQueueEligibilityPolicy,
    *,
    readiness: bool,
    missing_required_fields: Sequence[str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "project_id": policy.project_id,
        "provider": "linear",
        "external_work_item_id": str(issue.get("id", "")).strip(),
        "external_identifier": issue.get("identifier"),
        "title": issue.get("title"),
        "description": issue.get("description"),
        "url": issue.get("url"),
        "linear": _linear_metadata(issue, policy),
        "preflight": _ready_preflight(
            readiness=readiness,
            issue_context=not missing_required_fields,
        ),
    }
    priority = _field_value(issue, policy.fields.priority) if policy.fields.priority else None
    priority_label = (
        _field_value(issue, policy.fields.priority_label) if policy.fields.priority_label else None
    )
    risk = _field_value(issue, policy.fields.risk) if policy.fields.risk else None
    if priority is not None:
        payload["priority"] = priority
    if priority_label is not None:
        payload["priority_label"] = priority_label
    if risk is not None:
        payload["risk"] = risk
    if missing_required_fields:
        payload["status"] = "blocked"
        payload["missing_required_fields"] = list(missing_required_fields)
    return payload


async def poll_linear_delivery_queue(
    policy: LinearQueueEligibilityPolicy,
    *,
    client: LinearIssueClient,
) -> dict[str, Any]:
    result = client.list_issues(
        readiness_label=policy.readiness_label,
        team_ids=policy.team_ids,
        team_keys=policy.team_keys,
        team_names=policy.team_names,
        linear_project_ids=policy.linear_project_ids,
        linear_project_names=policy.linear_project_names,
        excluded_statuses=policy.excluded_statuses,
    )
    issues = await result if inspect.isawaitable(result) else result
    stats = {"status": "polled", "provider": "linear", "items": 0, "skipped": 0}
    for issue in issues:
        if not _in_scope(issue, policy) or _is_excluded_status(issue, policy):
            stats["skipped"] += 1
            continue
        readiness = _has_label(issue, policy.readiness_label)
        if not readiness and policy.missing_readiness == "skip":
            stats["skipped"] += 1
            continue
        missing_required_fields = _missing_required_fields(issue, policy)
        await delivery_queue.upsert_delivery_queue_item(
            _payload_for_issue(
                issue,
                policy,
                readiness=readiness,
                missing_required_fields=missing_required_fields,
            )
        )
        stats["items"] += 1
    return stats
