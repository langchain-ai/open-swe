"""Project-level model routing by delivery agent role."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from . import project_registry, project_secrets
from .utils.thread_ops import langgraph_client

MODEL_ROUTING_AUDIT_NAMESPACE: list[str] = ["delivery_project_model_routing_audit"]
MODEL_ROUTING_ROLES: frozenset[str] = frozenset(
    {
        "orchestrator",
        "explorer",
        "executor",
        "reviewer",
        "qa",
        "drupal_backend",
        "drupal_frontend",
        "content",
        "content_editor",
        "design",
        "qa_reviewer",
        "vision",
        "browser_proof",
        "helper",
        "subagent",
        "fallback",
    }
)


def _client():
    return langgraph_client()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _string(item)
        if text and text not in out:
            out.append(text)
    return out


def _capabilities(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    out: dict[str, Any] = {}
    for key in (
        "tool_calling",
        "vision",
        "reasoning",
        "json_schema_mode",
        "streaming",
    ):
        if key in raw:
            out[key] = bool(raw.get(key))
    context_window = raw.get("context_window")
    if isinstance(context_window, int) and context_window > 0:
        out["context_window"] = context_window
    cost = _mapping(raw.get("cost"))
    if cost:
        out["cost"] = {
            key: value
            for key, value in cost.items()
            if key in {"input_per_million", "output_per_million", "currency"}
            and (isinstance(value, int | float | str))
        }
    return out


def _fallback_chain(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        selection = _selection(item)
        if selection:
            out.append(selection)
    return out


def _selection(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str) and value.strip():
        return {"model_id": value.strip(), "effort": "medium"}
    if not isinstance(value, Mapping):
        return None
    model_id = value.get("model_id") or value.get("model")
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    effort = _string(value.get("effort")) or "medium"
    selection: dict[str, Any] = {
        "model_id": model_id.strip(),
        "effort": effort,
    }
    endpoint_id = _string(value.get("endpoint_id") or value.get("endpoint"))
    if endpoint_id:
        selection["endpoint_id"] = endpoint_id
    capabilities = _capabilities(value.get("capabilities"))
    if capabilities:
        selection["capabilities"] = capabilities
    fallback_chain = _fallback_chain(value.get("fallback_chain"))
    if fallback_chain:
        selection["fallback_chain"] = fallback_chain
    return selection


def _allowed_model_ids(project: Mapping[str, Any]) -> set[str]:
    policy = _mapping(project.get("ai_hub_policy"))
    raw = policy.get("model_ids") or policy.get("models") or []
    if not isinstance(raw, list):
        return set()
    return {model.strip() for model in raw if isinstance(model, str) and model.strip()}


def _routing_environment(project: Mapping[str, Any]) -> str:
    routing = _mapping(project.get("model_routing"))
    environment = _string(routing.get("environment"))
    if environment:
        return environment
    policy = _mapping(project.get("ai_hub_policy"))
    return _string(policy.get("environment")) or project_secrets.DEFAULT_AI_HUB_ENVIRONMENT


def _endpoint_records(project: Mapping[str, Any], environment: str) -> list[dict[str, Any]]:
    registry = _mapping(project.get("model_endpoint_registry"))
    environments = _mapping(registry.get("environments"))
    record = _mapping(environments.get(environment))
    return [
        dict(endpoint) for endpoint in record.get("endpoints", []) if isinstance(endpoint, Mapping)
    ]


def _endpoint_index(project: Mapping[str, Any], environment: str) -> dict[str, dict[str, Any]]:
    return {
        endpoint["id"]: endpoint
        for endpoint in _endpoint_records(project, environment)
        if isinstance(endpoint.get("id"), str)
    }


def _base_url_fingerprint(endpoint: Mapping[str, Any]) -> str:
    base_url = _string(endpoint.get("base_url"))
    return sha256(base_url.encode("utf-8")).hexdigest()[:12] if base_url else ""


def _model_available(endpoint: Mapping[str, Any], model_id: str) -> bool:
    models = _string_list(endpoint.get("model_ids"))
    return model_id in models or bool(endpoint.get("supports_model_discovery"))


def _endpoint_snapshot(endpoint: Mapping[str, Any], selection: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = {
        **dict(selection),
        "provider_type": _string(endpoint.get("provider_type")),
        "base_url_fingerprint": _base_url_fingerprint(endpoint),
    }
    if capabilities := _capabilities(selection.get("capabilities")):
        snapshot["capabilities"] = capabilities
    if fallback_chain := _fallback_chain(selection.get("fallback_chain")):
        snapshot["fallback_chain"] = fallback_chain
    return snapshot


def _model_option(endpoint: Mapping[str, Any], model_id: str) -> dict[str, Any]:
    capabilities = _mapping(endpoint.get("model_capabilities")).get(model_id)
    return {
        "model_id": model_id,
        "capabilities": _capabilities(capabilities),
    }


def model_routing_payload(project: Mapping[str, Any]) -> dict[str, Any]:
    environment = _routing_environment(project)
    routing = _normal_routing(_mapping(project.get("model_routing")))
    endpoints = []
    for endpoint in _endpoint_records(project, environment):
        endpoint_id = _string(endpoint.get("id"))
        if not endpoint_id:
            continue
        endpoints.append(
            {
                "id": endpoint_id,
                "display_name": _string(endpoint.get("display_name")) or endpoint_id,
                "provider_type": _string(endpoint.get("provider_type")),
                "disabled": bool(endpoint.get("disabled")),
                "base_url_fingerprint": _base_url_fingerprint(endpoint),
                "models": [
                    _model_option(endpoint, model_id)
                    for model_id in _string_list(endpoint.get("model_ids"))
                ],
                "supports_model_discovery": bool(endpoint.get("supports_model_discovery")),
            }
        )
    return {
        "project_id": project.get("project_id"),
        "environment": environment,
        "roles": sorted(MODEL_ROUTING_ROLES),
        "routing": routing,
        "endpoints": endpoints,
        "legacy_models": sorted(_allowed_model_ids(project)),
    }


def _normal_routing(routing: Mapping[str, Any]) -> dict[str, Any]:
    roles = _mapping(routing.get("roles"))
    normalized_roles = {
        role: selection
        for role, value in roles.items()
        if role in MODEL_ROUTING_ROLES and (selection := _selection(value)) is not None
    }
    normalized: dict[str, Any] = {"roles": normalized_roles}
    if environment := _string(routing.get("environment")):
        normalized["environment"] = environment
    if default := _selection(routing.get("default")):
        normalized["default"] = default
    if fallback := _selection(routing.get("fallback")):
        normalized["fallback"] = fallback
    return normalized


def validate_project_model_routing(project: Mapping[str, Any]) -> dict[str, Any]:
    routing = _normal_routing(_mapping(project.get("model_routing")))
    if not routing.get("default") and not routing.get("roles") and not routing.get("fallback"):
        return {"ready": True, "blockers": []}
    allowed = _allowed_model_ids(project)
    environment = _routing_environment(project)
    endpoints = _endpoint_index(project, environment)
    blockers = []
    for role, selection in _iter_routing_selections(routing):
        model_id = selection["model_id"]
        endpoint_id = _string(selection.get("endpoint_id"))
        if endpoint_id:
            endpoint = endpoints.get(endpoint_id)
            if endpoint is None:
                blockers.append(
                    {
                        "code": f"missing_endpoint:{role}",
                        "message": f"Model endpoint {endpoint_id} is not configured.",
                    }
                )
            elif endpoint.get("disabled") is True:
                blockers.append(
                    {
                        "code": f"disabled_endpoint:{role}",
                        "message": f"Model endpoint {endpoint_id} is disabled.",
                    }
                )
            elif not _model_available(endpoint, model_id):
                blockers.append(
                    {
                        "code": f"invalid_model:{role}",
                        "message": f"Model {model_id} is not configured on endpoint {endpoint_id}.",
                    }
                )
        elif model_id not in allowed:
            blockers.append(
                {
                    "code": f"invalid_model:{role}",
                    "message": f"Model {model_id} is not in the project's AI Hub model list.",
                }
            )
    return {"ready": not blockers, "blockers": blockers}


async def validate_project_model_routing_ready(project: Mapping[str, Any]) -> dict[str, Any]:
    result = validate_project_model_routing(project)
    blockers = list(result["blockers"])
    project_id = _string(project.get("project_id"))
    environment = _routing_environment(project)
    endpoints = _endpoint_index(project, environment)
    for role, selection in _iter_routing_selections(
        _normal_routing(_mapping(project.get("model_routing")))
    ):
        endpoint_id = _string(selection.get("endpoint_id"))
        endpoint = endpoints.get(endpoint_id)
        if not project_id or endpoint is None or endpoint.get("disabled") is True:
            continue
        if _string(endpoint.get("auth_type")) == "none":
            continue
        secret_name = _string(endpoint.get("secret_name"))
        if not secret_name:
            blockers.append(
                {
                    "code": f"missing_secret_ref:{role}",
                    "message": f"Model endpoint {endpoint_id} has no secret reference.",
                }
            )
        elif not await project_secrets.resolve_project_secret(
            project_id,
            environment=environment,
            name=secret_name,
        ):
            blockers.append(
                {
                    "code": f"missing_endpoint_secret:{role}",
                    "message": f"Project secret {secret_name} is missing for endpoint {endpoint_id}.",
                }
            )
    return {"ready": not blockers, "blockers": blockers}


def _iter_routing_selections(routing: Mapping[str, Any]):
    default = _selection(routing.get("default"))
    if default:
        yield "default", default
    for role, value in _mapping(routing.get("roles")).items():
        if selection := _selection(value):
            yield role, selection
    fallback = _selection(routing.get("fallback"))
    if fallback:
        yield "fallback", fallback


def resolve_model_for_role(project: Mapping[str, Any], role: str) -> dict[str, Any] | None:
    routing = _normal_routing(_mapping(project.get("model_routing")))
    role = role.strip()
    roles = _mapping(routing.get("roles"))
    if role in roles and (selection := _selection(roles[role])):
        return {"role": role, **selection, "source": "role"}
    if default := _selection(routing.get("default")):
        return {"role": role, **default, "source": "default"}
    if fallback := _selection(routing.get("fallback")):
        return {"role": role, **fallback, "source": "fallback"}
    return None


def build_model_routing_snapshot(
    project: Mapping[str, Any],
    roles: tuple[str, ...],
) -> dict[str, Any]:
    environment = _routing_environment(project)
    endpoints = _endpoint_index(project, environment)
    resolved_roles: dict[str, Any] = {}
    for role in roles:
        selection = resolve_model_for_role(project, role)
        if not selection:
            continue
        endpoint_id = _string(selection.get("endpoint_id"))
        endpoint = endpoints.get(endpoint_id)
        resolved_roles[role] = (
            _endpoint_snapshot(endpoint, selection) if endpoint is not None else selection
        )
    return {
        "project_id": project.get("project_id"),
        "environment": environment,
        "roles": resolved_roles,
    }


async def _audit_model_change(
    *,
    project_id: str,
    actor: str,
    role: str,
    previous: str | None,
    new: str | None,
) -> None:
    record = {
        "project_id": project_id,
        "actor": actor,
        "role": role,
        "previous_model_id": previous,
        "new_model_id": new,
        "created_at": _now_iso(),
    }
    key = f"{project_id}:{record['created_at']}:{secrets.token_hex(8)}"
    await _client().store.put_item(MODEL_ROUTING_AUDIT_NAMESPACE, key, record)


def _model_for_audit(routing: Mapping[str, Any], role: str) -> str | None:
    if role == "default":
        selection = _selection(routing.get("default"))
    elif role == "fallback":
        selection = _selection(routing.get("fallback"))
    else:
        selection = _selection(_mapping(routing.get("roles")).get(role))
    if not selection:
        return None
    endpoint_id = _string(selection.get("endpoint_id"))
    return f"{endpoint_id}:{selection['model_id']}" if endpoint_id else selection["model_id"]


async def set_project_model_routing(
    project_id: str,
    routing: Mapping[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    project = await project_registry.get_delivery_project(project_id)
    if project is None:
        raise KeyError(f"delivery project not found: {project_id}")
    normalized = _normal_routing(routing)
    candidate = {**project, "model_routing": normalized}
    validation = validate_project_model_routing(candidate)
    if not validation["ready"]:
        raise ValueError(validation["blockers"][0]["message"])

    previous = _mapping(project.get("model_routing"))
    roles = {
        "default",
        "fallback",
        *_mapping(normalized.get("roles")).keys(),
        *_mapping(previous.get("roles")).keys(),
    }
    for role in sorted(roles):
        before = _model_for_audit(previous, role)
        after = _model_for_audit(normalized, role)
        if before != after:
            await _audit_model_change(
                project_id=project_id,
                actor=actor,
                role=role,
                previous=before,
                new=after,
            )
    return await project_registry.upsert_delivery_project(
        {"project_id": project_id, "model_routing": normalized}
    )


async def list_model_routing_audit(project_id: str) -> list[dict[str, Any]]:
    result = await _client().store.search_items(
        MODEL_ROUTING_AUDIT_NAMESPACE,
        filter={"project_id": project_id},
        limit=1000,
    )
    items = result.get("items") if isinstance(result, dict) else getattr(result, "items", [])
    records = []
    for item in items or []:
        value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
        if isinstance(value, dict):
            records.append(value)
    return sorted(records, key=lambda item: str(item.get("created_at", "")))
