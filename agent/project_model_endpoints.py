"""Workspace-scoped model endpoint registry."""

from __future__ import annotations

import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException

from . import project_registry, project_secrets

MODEL_ENDPOINT_PROVIDER_TYPES = {
    "ai_hub",
    "deepseek",
    "zai",
    "openai_compatible",
    "opencode",
}
MODEL_ENDPOINT_AUTH_TYPES = {"bearer", "api_key", "none"}
MODEL_ENDPOINT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")
DEFAULT_MODEL_ENDPOINT_ENVIRONMENT = project_secrets.DEFAULT_AI_HUB_ENVIRONMENT


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _normalize_environment(environment: str | None) -> str:
    value = (environment or DEFAULT_MODEL_ENDPOINT_ENVIRONMENT).strip().lower()
    if not value:
        raise HTTPException(422, "environment is required")
    return value


def _normalize_endpoint_id(value: str | None, display_name: str) -> str:
    candidate = _string(value)
    if not candidate:
        base = re.sub(r"[^a-z0-9_-]+", "-", display_name.strip().lower()).strip("-")
        candidate = f"{base or 'endpoint'}-{secrets.token_hex(3)}"
    if not MODEL_ENDPOINT_ID_PATTERN.match(candidate):
        raise HTTPException(
            422, "endpoint id must be lowercase letters, numbers, dash, or underscore"
        )
    return candidate


def _normalize_url(value: str) -> str:
    value = value.rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(422, "base URL must be an http(s) URL")
    return value


def _normalize_headers(value: Any) -> dict[str, str]:
    headers = _mapping(value)
    normalized: dict[str, str] = {}
    for key, item in headers.items():
        name = _string(key)
        text = _string(item)
        if name and text:
            normalized[name] = text
    return normalized


def _normalize_rate_limit(value: Any) -> dict[str, int]:
    raw = _mapping(value)
    normalized: dict[str, int] = {}
    for key in ("requests_per_minute", "tokens_per_minute"):
        item = raw.get(key)
        if isinstance(item, int) and item > 0:
            normalized[key] = item
    return normalized


def endpoint_preset(provider_type: str) -> dict[str, Any]:
    provider_type = provider_type.strip().lower()
    if provider_type == "ai_hub":
        return {
            "display_name": "AI Hub",
            "provider_type": "ai_hub",
            "base_url": "https://api.openai.com/v1",
            "api_path": "/chat/completions",
            "auth_type": "bearer",
            "secret_name": project_secrets.AI_HUB_API_KEY_SECRET,
            "model_ids": [],
            "supports_model_discovery": True,
        }
    if provider_type == "deepseek":
        return {
            "display_name": "DeepSeek",
            "provider_type": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api_path": "/chat/completions",
            "auth_type": "bearer",
            "secret_name": "DEEPSEEK_API_KEY",
            "model_ids": ["deepseek-chat", "deepseek-reasoner"],
            "supports_model_discovery": True,
        }
    if provider_type == "zai":
        return {
            "display_name": "Z.AI",
            "provider_type": "zai",
            "base_url": "https://api.z.ai/api/paas/v4",
            "api_path": "/chat/completions",
            "auth_type": "bearer",
            "secret_name": "ZAI_API_KEY",
            "model_ids": ["glm-4.5"],
            "supports_model_discovery": True,
        }
    if provider_type == "opencode":
        return {
            "display_name": "OpenCode compatible",
            "provider_type": "opencode",
            "base_url": "http://localhost:4096/v1",
            "api_path": "/chat/completions",
            "auth_type": "bearer",
            "secret_name": "OPENCODE_API_KEY",
            "model_ids": [],
            "supports_model_discovery": True,
        }
    if provider_type == "openai_compatible":
        return {
            "display_name": "Custom OpenAI-compatible",
            "provider_type": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "api_path": "/chat/completions",
            "auth_type": "bearer",
            "secret_name": "CUSTOM_MODEL_API_KEY",
            "model_ids": [],
            "supports_model_discovery": True,
        }
    raise HTTPException(422, "unsupported model endpoint provider")


def endpoint_presets() -> list[dict[str, Any]]:
    return [
        endpoint_preset("ai_hub"),
        endpoint_preset("deepseek"),
        endpoint_preset("zai"),
        endpoint_preset("openai_compatible"),
        endpoint_preset("opencode"),
    ]


def normalize_endpoint(
    payload: Mapping[str, Any], *, existing: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    current = _mapping(existing)
    merged = {**current, **dict(payload)}
    display_name = _string(merged.get("display_name") or merged.get("name"))
    if not display_name:
        raise HTTPException(422, "display name is required")
    provider_type = _string(merged.get("provider_type")).lower()
    if provider_type not in MODEL_ENDPOINT_PROVIDER_TYPES:
        raise HTTPException(422, "unsupported model endpoint provider")
    auth_type = _string(merged.get("auth_type")).lower() or "bearer"
    if auth_type not in MODEL_ENDPOINT_AUTH_TYPES:
        raise HTTPException(422, "unsupported model endpoint auth type")
    model_ids = _string_list(merged.get("model_ids") or merged.get("models"))
    timeout = merged.get("timeout_seconds")
    timeout_seconds = timeout if isinstance(timeout, int) and timeout > 0 else 60
    now = _now_iso()
    return {
        "id": _normalize_endpoint_id(_string(merged.get("id")), display_name),
        "display_name": display_name,
        "provider_type": provider_type,
        "base_url": _normalize_url(_string(merged.get("base_url"))),
        "api_path": _string(merged.get("api_path")) or "/chat/completions",
        "auth_type": auth_type,
        "secret_name": _string(merged.get("secret_name")),
        "default_headers": _normalize_headers(merged.get("default_headers")),
        "model_ids": model_ids,
        "organization": _string(merged.get("organization")),
        "project": _string(merged.get("project")),
        "timeout_seconds": timeout_seconds,
        "rate_limit": _normalize_rate_limit(merged.get("rate_limit")),
        "supports_model_discovery": bool(merged.get("supports_model_discovery", True)),
        "disabled": bool(merged.get("disabled", False)),
        "created_at": current.get("created_at") or now,
        "updated_at": now,
    }


def _registry(project: Mapping[str, Any]) -> dict[str, Any]:
    return _mapping(project.get("model_endpoint_registry"))


def _environment_record(project: Mapping[str, Any], environment: str) -> dict[str, Any]:
    registry = _registry(project)
    environments = _mapping(registry.get("environments"))
    return _mapping(environments.get(environment))


async def list_model_endpoints(
    project_id: str,
    *,
    environment: str | None,
) -> dict[str, Any]:
    project = await project_registry.get_delivery_project(project_id)
    if project is None:
        raise KeyError(f"delivery project not found: {project_id}")
    environment = _normalize_environment(environment)
    record = _environment_record(project, environment)
    endpoints = [
        await redact_endpoint(project_id, environment=environment, endpoint=endpoint)
        for endpoint in record.get("endpoints", [])
        if isinstance(endpoint, Mapping)
    ]
    return {"project_id": project_id, "environment": environment, "items": endpoints}


async def redact_endpoint(
    project_id: str,
    *,
    environment: str,
    endpoint: Mapping[str, Any],
) -> dict[str, Any]:
    secret_name = _string(endpoint.get("secret_name"))
    secret_status = (
        await project_secrets.test_project_secret(
            project_id,
            environment=environment,
            name=secret_name,
        )
        if secret_name
        else {"ready": endpoint.get("auth_type") == "none", "name": secret_name}
    )
    return {
        key: value for key, value in dict(endpoint).items() if key not in {"default_headers"}
    } | {
        "default_headers": sorted(_normalize_headers(endpoint.get("default_headers")).keys()),
        "secret": {
            "name": secret_name,
            "connected": bool(secret_status.get("ready")),
            "environment": environment,
        },
    }


async def upsert_model_endpoint(
    project_id: str,
    *,
    environment: str | None,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    project = await project_registry.get_delivery_project(project_id)
    if project is None:
        raise KeyError(f"delivery project not found: {project_id}")
    environment = _normalize_environment(environment)
    registry = _registry(project)
    environments = _mapping(registry.get("environments"))
    current_record = _mapping(environments.get(environment))
    current_endpoints = [
        dict(endpoint)
        for endpoint in current_record.get("endpoints", [])
        if isinstance(endpoint, Mapping)
    ]
    endpoint_id = _string(payload.get("id"))
    existing = next(
        (endpoint for endpoint in current_endpoints if endpoint.get("id") == endpoint_id), None
    )
    endpoint = normalize_endpoint(payload, existing=existing)
    endpoints = [item for item in current_endpoints if item.get("id") != endpoint["id"]]
    endpoints.append(endpoint)
    environments[environment] = {"endpoints": sorted(endpoints, key=lambda item: item["id"])}
    updated = await project_registry.upsert_delivery_project(
        {
            "project_id": project_id,
            "model_endpoint_registry": {"environments": environments},
        }
    )
    endpoint_id = endpoint["id"]
    stored = next(
        item
        for item in _environment_record(updated, environment).get("endpoints", [])
        if isinstance(item, Mapping) and item.get("id") == endpoint_id
    )
    return await redact_endpoint(project_id, environment=environment, endpoint=stored)


async def delete_model_endpoint(
    project_id: str,
    *,
    environment: str | None,
    endpoint_id: str,
) -> dict[str, Any]:
    project = await project_registry.get_delivery_project(project_id)
    if project is None:
        raise KeyError(f"delivery project not found: {project_id}")
    environment = _normalize_environment(environment)
    registry = _registry(project)
    environments = _mapping(registry.get("environments"))
    record = _mapping(environments.get(environment))
    endpoints = [
        dict(endpoint)
        for endpoint in record.get("endpoints", [])
        if isinstance(endpoint, Mapping) and endpoint.get("id") != endpoint_id
    ]
    environments[environment] = {"endpoints": endpoints}
    await project_registry.upsert_delivery_project(
        {
            "project_id": project_id,
            "model_endpoint_registry": {"environments": environments},
        }
    )
    return {
        "deleted": True,
        "project_id": project_id,
        "environment": environment,
        "id": endpoint_id,
    }


async def validate_model_endpoint(
    project_id: str,
    *,
    environment: str | None,
    endpoint_id: str,
) -> dict[str, Any]:
    project = await project_registry.get_delivery_project(project_id)
    if project is None:
        raise KeyError(f"delivery project not found: {project_id}")
    environment = _normalize_environment(environment)
    endpoints = [
        endpoint
        for endpoint in _environment_record(project, environment).get("endpoints", [])
        if isinstance(endpoint, Mapping)
    ]
    endpoint = next((item for item in endpoints if item.get("id") == endpoint_id), None)
    if endpoint is None:
        raise KeyError(f"model endpoint not found: {endpoint_id}")
    blockers = []
    if endpoint.get("disabled") is True:
        blockers.append({"code": "endpoint_disabled", "message": "Endpoint is disabled."})
    if _string(endpoint.get("auth_type")) != "none":
        secret_name = _string(endpoint.get("secret_name"))
        if not secret_name:
            blockers.append(
                {"code": "missing_secret_ref", "message": "Secret reference is missing."}
            )
        elif not await project_secrets.resolve_project_secret(
            project_id,
            environment=environment,
            name=secret_name,
        ):
            blockers.append(
                {
                    "code": "missing_secret",
                    "message": f"Project secret {secret_name} is missing.",
                }
            )
    if not _string_list(endpoint.get("model_ids")) and not bool(
        endpoint.get("supports_model_discovery")
    ):
        blockers.append(
            {
                "code": "missing_models",
                "message": "Add manual models or enable model discovery.",
            }
        )
    return {
        "ready": not blockers,
        "project_id": project_id,
        "environment": environment,
        "id": endpoint_id,
        "blockers": blockers,
        "models": _string_list(endpoint.get("model_ids")),
        "model_discovery": bool(endpoint.get("supports_model_discovery")),
    }
