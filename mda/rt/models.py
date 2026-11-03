"""Chat models for the models Open SWE picked when it dispatched a run.

The backend sends model ids and provider settings, never credentials; keys come
from this deployment's environment. Both sides build a model with
``runtime_api.models``, so its provider settings, gateway routing and context
window are the ones the backend picked it with.
"""

import json
from functools import lru_cache

from langchain_core.language_models import BaseChatModel
from pydantic import JsonValue, TypeAdapter
from runtime_api.catalog import ModelSpec
from runtime_api.models import build_chat_model

_kwargs = TypeAdapter(dict[str, JsonValue])


@lru_cache(maxsize=32)
def _build(model_id: str, kwargs_json: str, use_gateway: bool) -> BaseChatModel:
    kwargs = _kwargs.validate_python(json.loads(kwargs_json))
    return build_chat_model(model_id, kwargs, use_gateway=use_gateway)


def build_model(spec: ModelSpec, *, use_gateway: bool) -> BaseChatModel:
    """The chat model for ``spec``, built once per distinct model and settings."""
    return _build(spec.model_id, json.dumps(spec.kwargs, sort_keys=True), use_gateway)
