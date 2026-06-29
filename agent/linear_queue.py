"""Linear delivery queue polling."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from . import delivery_queue, project_registry
from .dashboard.provider_pat_vault import resolve_provider_pat
from .utils.linear import _graphql_request

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


class LinearCatalogClient(Protocol):
    def list_catalog(self) -> Awaitable[Mapping[str, Any]] | Mapping[str, Any]: ...


class LinearGraphQLIssueClient:
    def __init__(
        self,
        *,
        token: str | None = None,
        page_size: int = 100,
        max_pages: int = 10,
    ) -> None:
        self.token = token
        self.page_size = page_size
        self.max_pages = max_pages

    async def list_issues(
        self,
        *,
        readiness_label: str,
        team_ids: Sequence[str],
        team_keys: Sequence[str],
        team_names: Sequence[str],
        linear_project_ids: Sequence[str],
        linear_project_names: Sequence[str],
        excluded_statuses: Sequence[str],
    ) -> list[Mapping[str, Any]]:
        query = """
        query DeliveryQueueIssues($first: Int!, $after: String) {
          issues(first: $first, after: $after) {
            nodes {
              id
              identifier
              title
              description
              url
              priority
              priorityLabel
              state { id name type }
              team { id key name }
              project { id name }
              labels { nodes { id name } }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
        """
        issues: list[Mapping[str, Any]] = []
        cursor: str | None = None
        for _ in range(self.max_pages):
            result = await _graphql_request(
                query,
                {"first": self.page_size, "after": cursor},
                token=self.token,
            )
            if "error" in result:
                raise RuntimeError(f"linear issue polling failed: {result['error']}")
            connection = _mapping(result.get("issues"))
            nodes = connection.get("nodes") or []
            if isinstance(nodes, Sequence) and not isinstance(nodes, str):
                issues.extend(issue for issue in nodes if isinstance(issue, Mapping))
            page_info = _mapping(connection.get("pageInfo"))
            if not page_info.get("hasNextPage"):
                break
            next_cursor = page_info.get("endCursor")
            cursor = next_cursor if isinstance(next_cursor, str) and next_cursor else None
            if cursor is None:
                break
        return issues


class LinearGraphQLCatalogClient:
    def __init__(self, *, token: str | None = None) -> None:
        self.token = token

    async def list_catalog(self) -> Mapping[str, Any]:
        query = """
        query DeliveryQueueCatalog {
          teams {
            nodes { id key name }
          }
          projects {
            nodes { id name }
          }
        }
        """
        result = await _graphql_request(query, token=self.token)
        if "error" in result:
            raise RuntimeError(f"linear connection failed: {result['error']}")
        return {
            "teams": _mapping(result.get("teams")).get("nodes") or [],
            "projects": _mapping(result.get("projects")).get("nodes") or [],
        }


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


def _string_tuple(*values: Any) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        if isinstance(value, str):
            if value.strip():
                out.append(value.strip())
        elif isinstance(value, Sequence):
            out.extend(item.strip() for item in value if isinstance(item, str) and item.strip())
    return tuple(out)


def _readiness_label(policy: Mapping[str, Any]) -> str:
    labels = _string_tuple(policy.get("readiness_label"), policy.get("labels"))
    return labels[0] if labels else "agent-ready"


def _missing_readiness_action(value: Any) -> ReadinessMissingAction:
    return "not-ready" if value == "not-ready" else "skip"


def linear_policy_from_project(project: Mapping[str, Any]) -> LinearQueueEligibilityPolicy:
    queue_policy = _mapping(project.get("queue_eligibility_policy"))
    tracker = _mapping(project.get("tracker"))
    tracker_config = _mapping(tracker.get("config"))
    return LinearQueueEligibilityPolicy(
        project_id=str(project.get("project_id", "")).strip(),
        readiness_label=_readiness_label(queue_policy),
        missing_readiness=_missing_readiness_action(queue_policy.get("missing_readiness")),
        excluded_statuses=_string_tuple(
            queue_policy.get("excluded_statuses"),
            queue_policy.get("exclude_statuses"),
        ),
        required_fields=_string_tuple(queue_policy.get("required_fields")),
        team_ids=_string_tuple(
            queue_policy.get("team_ids"),
            tracker_config.get("team_ids"),
            tracker_config.get("team_id"),
        ),
        team_keys=_string_tuple(
            queue_policy.get("team_keys"),
            tracker_config.get("team_keys"),
            tracker_config.get("team_key"),
        ),
        team_names=_string_tuple(
            queue_policy.get("team_names"),
            tracker_config.get("team_names"),
            tracker_config.get("team_name"),
        ),
        linear_project_ids=_string_tuple(
            queue_policy.get("linear_project_ids"),
            queue_policy.get("project_ids"),
            tracker_config.get("linear_project_ids"),
            tracker_config.get("project_ids"),
            tracker_config.get("project_id"),
        ),
        linear_project_names=_string_tuple(
            queue_policy.get("linear_project_names"),
            queue_policy.get("project_names"),
            tracker_config.get("linear_project_names"),
            tracker_config.get("project_names"),
            tracker_config.get("project_name"),
        ),
    )


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


def _preview_for_issue(
    issue: Mapping[str, Any],
    policy: LinearQueueEligibilityPolicy,
) -> dict[str, Any]:
    in_scope = _in_scope(issue, policy)
    excluded = _is_excluded_status(issue, policy)
    readiness = _has_label(issue, policy.readiness_label)
    missing_required_fields = _missing_required_fields(issue, policy)
    if not in_scope:
        action = "ignored"
        reason = "out_of_scope"
    elif excluded:
        action = "ignored"
        reason = "excluded_status"
    elif not readiness and policy.missing_readiness == "skip":
        action = "ignored"
        reason = "missing_readiness"
    elif missing_required_fields:
        action = "blocked"
        reason = "missing_required_fields"
    elif not readiness:
        action = "not-ready"
        reason = "missing_readiness"
    else:
        action = "queued"
        reason = "ready"
    return {
        "action": action,
        "reason": reason,
        "identifier": issue.get("identifier"),
        "title": issue.get("title"),
        "url": issue.get("url"),
        "team": dict(_mapping(issue.get("team"))),
        "project": dict(_mapping(issue.get("project"))),
        "state": dict(_mapping(issue.get("state"))),
        "labels": [dict(label) for label in _labels(issue)],
        "missing_required_fields": list(missing_required_fields),
    }


async def preview_linear_delivery_queue(
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
    items = [_preview_for_issue(issue, policy) for issue in issues]
    counts = {"queued": 0, "not-ready": 0, "blocked": 0, "ignored": 0}
    for item in items:
        action = str(item.get("action"))
        if action in counts:
            counts[action] += 1
    return {
        "status": "previewed",
        "provider": "linear",
        "counts": counts,
        "items": items,
    }


async def test_linear_connection(
    *,
    client: LinearCatalogClient | None = None,
) -> dict[str, Any]:
    client = client or LinearGraphQLCatalogClient()
    result = client.list_catalog()
    catalog = await result if inspect.isawaitable(result) else result
    teams = catalog.get("teams") if isinstance(catalog, Mapping) else []
    projects = catalog.get("projects") if isinstance(catalog, Mapping) else []
    return {
        "status": "connected",
        "provider": "linear",
        "teams": list(teams) if isinstance(teams, Sequence) and not isinstance(teams, str) else [],
        "projects": (
            list(projects)
            if isinstance(projects, Sequence) and not isinstance(projects, str)
            else []
        ),
    }


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


def _project_member_logins(project: Mapping[str, Any]) -> list[str]:
    membership = _mapping(project.get("membership"))
    users = membership.get("users")
    if not isinstance(users, Sequence) or isinstance(users, str):
        return []
    return [user.strip().lower() for user in users if isinstance(user, str) and user.strip()]


async def _linear_issue_client_for_project(project: Mapping[str, Any]) -> LinearGraphQLIssueClient:
    project_id = str(project.get("project_id") or "")
    for login in _project_member_logins(project):
        resolved = await resolve_provider_pat(
            login,
            provider="linear",
            project_id=project_id,
            action="queue_poll",
        )
        if resolved is not None:
            return LinearGraphQLIssueClient(token=resolved.token)
    return LinearGraphQLIssueClient()


async def poll_configured_linear_delivery_queues(
    *,
    client: LinearIssueClient | None = None,
    projects: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if projects is None:
        projects = await project_registry.list_delivery_projects({"active": True})
    stats: dict[str, Any] = {
        "status": "polled",
        "provider": "linear",
        "projects": 0,
        "items": 0,
        "skipped": 0,
        "errors": [],
    }
    for project in projects:
        tracker = _mapping(project.get("tracker"))
        if tracker.get("provider") != "linear":
            continue
        stats["projects"] += 1
        try:
            project_client = client or await _linear_issue_client_for_project(project)
            result = await poll_linear_delivery_queue(
                linear_policy_from_project(project),
                client=project_client,
            )
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append({"project_id": project.get("project_id"), "message": str(exc)})
            continue
        stats["items"] += result["items"]
        stats["skipped"] += result["skipped"]
    if stats["errors"]:
        stats["status"] = "error"
    return stats
