"""Model endpoint adapter boundary for workspace-configured providers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from . import project_secrets

EndpointInvocationStyle = Literal["chat_completions", "responses"]


@dataclass(frozen=True)
class AdapterError:
    code: str
    message: str
    provider_status: int | None = None


@dataclass(frozen=True)
class EndpointRuntimeConfig:
    model: str
    base_url: str
    api_key: str | None
    default_headers: dict[str, str]
    timeout_seconds: int
    invocation_style: EndpointInvocationStyle

    def langchain_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "timeout": self.timeout_seconds,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.default_headers:
            kwargs["default_headers"] = self.default_headers
        if self.invocation_style == "responses":
            kwargs["use_responses_api"] = True
        return kwargs


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


def _headers(endpoint: Mapping[str, Any], secret: str | None) -> dict[str, str]:
    headers = {
        str(key): str(value)
        for key, value in _mapping(endpoint.get("default_headers")).items()
        if _string(str(key)) and _string(str(value))
    }
    auth_type = _string(endpoint.get("auth_type")) or "bearer"
    if secret and auth_type == "bearer":
        headers["Authorization"] = f"Bearer {secret}"
    elif secret and auth_type == "api_key":
        headers["X-API-Key"] = secret
    return headers


def _runtime_headers(endpoint: Mapping[str, Any], secret: str | None) -> dict[str, str]:
    headers = {
        str(key): str(value)
        for key, value in _mapping(endpoint.get("default_headers")).items()
        if _string(str(key)) and _string(str(value))
    }
    if secret and (_string(endpoint.get("auth_type")) or "bearer") == "api_key":
        headers["X-API-Key"] = secret
    return headers


def _invocation_style(endpoint: Mapping[str, Any]) -> EndpointInvocationStyle:
    api_path = _string(endpoint.get("api_path"))
    return "responses" if "responses" in api_path else "chat_completions"


def _provider_error(exc: Exception) -> AdapterError:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return AdapterError(
                "invalid_token", "Provider rejected the endpoint credentials.", status
            )
        if status == 404:
            return AdapterError(
                "invalid_model", "Provider did not find the requested model.", status
            )
        return AdapterError(
            "provider_error",
            f"Provider returned HTTP {status} during endpoint validation.",
            status,
        )
    if isinstance(exc, httpx.HTTPError):
        return AdapterError("provider_unreachable", "Provider endpoint could not be reached.")
    return AdapterError("provider_error", "Provider validation failed.")


class OpenAICompatibleEndpointAdapter:
    """Generic adapter for OpenAI-compatible endpoint definitions."""

    async def resolve_secret(
        self,
        project_id: str,
        *,
        environment: str,
        endpoint: Mapping[str, Any],
    ) -> str | None:
        if _string(endpoint.get("auth_type")) == "none":
            return None
        secret_name = _string(endpoint.get("secret_name"))
        if not secret_name:
            return None
        return await project_secrets.resolve_project_secret(
            project_id,
            environment=environment,
            name=secret_name,
        )

    def runtime_config(
        self,
        endpoint: Mapping[str, Any],
        *,
        model_id: str,
        secret: str | None,
    ) -> EndpointRuntimeConfig:
        return EndpointRuntimeConfig(
            model=model_id,
            base_url=_string(endpoint.get("base_url")),
            api_key=secret,
            default_headers=_runtime_headers(endpoint, secret),
            timeout_seconds=int(endpoint.get("timeout_seconds") or 60),
            invocation_style=_invocation_style(endpoint),
        )

    async def discover_models(
        self,
        endpoint: Mapping[str, Any],
        *,
        secret: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> tuple[list[str], AdapterError | None]:
        if not bool(endpoint.get("supports_model_discovery")):
            return _string_list(endpoint.get("model_ids")), None
        url = f"{_string(endpoint.get('base_url')).rstrip('/')}/models"
        timeout = float(endpoint.get("timeout_seconds") or 60)
        try:
            async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
                response = await client.get(url, headers=_headers(endpoint, secret))
                response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            return [], _provider_error(exc)
        items = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(items, list):
            return [], AdapterError(
                "invalid_model_list", "Provider model list response is invalid."
            )
        models = [_string(item.get("id")) for item in items if isinstance(item, Mapping)]
        return [model for model in models if model], None

    async def validate(
        self,
        endpoint: Mapping[str, Any],
        *,
        project_id: str,
        environment: str,
        requested_model: str | None = None,
        transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
    ) -> dict[str, Any]:
        blockers: list[dict[str, Any]] = []
        if endpoint.get("disabled") is True:
            blockers.append({"code": "endpoint_disabled", "message": "Endpoint is disabled."})
        auth_type = _string(endpoint.get("auth_type")) or "bearer"
        secret = await self.resolve_secret(project_id, environment=environment, endpoint=endpoint)
        if auth_type != "none" and not _string(endpoint.get("secret_name")):
            blockers.append(
                {"code": "missing_secret_ref", "message": "Secret reference is missing."}
            )
        elif auth_type != "none" and not secret:
            blockers.append(
                {
                    "code": "missing_secret",
                    "message": f"Project secret {_string(endpoint.get('secret_name'))} is missing.",
                }
            )
        manual_models = _string_list(endpoint.get("model_ids"))
        models = manual_models
        if not blockers and not manual_models:
            transport = transport_factory() if transport_factory else None
            discovered, error = await self.discover_models(
                endpoint,
                secret=secret,
                transport=transport,
            )
            if error:
                blockers.append(
                    {
                        "code": error.code,
                        "message": error.message,
                        "provider_status": error.provider_status,
                    }
                )
            elif discovered:
                models = discovered
        if not models and not bool(endpoint.get("supports_model_discovery")):
            blockers.append(
                {
                    "code": "missing_models",
                    "message": "Add manual models or enable model discovery.",
                }
            )
        requested = _string(requested_model)
        if requested and models and requested not in models:
            blockers.append(
                {
                    "code": "invalid_model",
                    "message": f"Model {requested} is not available on this endpoint.",
                }
            )
        return {
            "ready": not blockers,
            "blockers": blockers,
            "models": models,
            "model_discovery": bool(endpoint.get("supports_model_discovery")),
        }


def adapter_for_endpoint(endpoint: Mapping[str, Any]) -> OpenAICompatibleEndpointAdapter:
    provider_type = _string(endpoint.get("provider_type"))
    if provider_type in {"ai_hub", "deepseek", "zai", "openai_compatible", "opencode"}:
        return OpenAICompatibleEndpointAdapter()
    return OpenAICompatibleEndpointAdapter()
