"""Chat models for the models Open SWE picked when it dispatched a run.

The backend sends model ids and provider settings, never credentials; keys come
from this deployment's environment, and gateway routing mirrors the backend's.
"""

import json
import os
from functools import lru_cache
from typing import Final

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, JsonValue, TypeAdapter

_DEFAULT_GATEWAY_BASE_URL: Final = "https://gateway.smith.langchain.com"
_GATEWAY_PROVIDER_PATHS: Final = {
    "openai": "/openai/v1",
    "anthropic": "/anthropic",
    "baseten": "/baseten/v1",
    "fireworks": "/fireworks",
    "google_genai": "/gemini",
}
_DEFAULT_MAX_RETRIES: Final = 6
_DEFAULT_REQUEST_TIMEOUT_SECONDS: Final = 600.0
_kwargs = TypeAdapter(dict[str, JsonValue])


class ModelSpec(BaseModel):
    model_id: str
    kwargs: dict[str, JsonValue]


class RunModels(BaseModel):
    model: ModelSpec
    subagent_model: ModelSpec
    use_gateway: bool


def _gateway_overrides(provider: str) -> dict[str, JsonValue]:
    path = _GATEWAY_PROVIDER_PATHS.get(provider)
    api_key = os.environ.get("LANGSMITH_GATEWAY_API_KEY") or os.environ.get("LANGSMITH_API_KEY")
    if path is None or not api_key:
        return {}
    base = (os.environ.get("LANGSMITH_GATEWAY_BASE_URL") or _DEFAULT_GATEWAY_BASE_URL).rstrip("/")
    return {"base_url": f"{base}{path}", "api_key": api_key}


@lru_cache(maxsize=32)
def _build(model_id: str, kwargs_json: str, use_gateway: bool) -> BaseChatModel:
    kwargs = _kwargs.validate_python(json.loads(kwargs_json))
    kwargs.setdefault("max_retries", _DEFAULT_MAX_RETRIES)
    kwargs.setdefault("timeout", _DEFAULT_REQUEST_TIMEOUT_SECONDS)
    provider = model_id.split(":", 1)[0]
    if provider == "openai":
        kwargs["use_responses_api"] = True
        kwargs.setdefault("store", False)
        kwargs.setdefault("output_version", "responses/v1")
        kwargs.setdefault("include", ["reasoning.encrypted_content"])
    if use_gateway:
        kwargs.update(_gateway_overrides(provider))
    return init_chat_model(model_id, **kwargs)


def build_model(spec: ModelSpec, *, use_gateway: bool) -> BaseChatModel:
    """The chat model for ``spec``, built once per distinct model and settings."""
    return _build(spec.model_id, json.dumps(spec.kwargs, sort_keys=True), use_gateway)
