"""Chat models for the models Open SWE picked when it dispatched a run.

The backend sends model ids and provider settings, never credentials; keys come
from this deployment's environment. Both sides build a model with
``runtime_api.models``, so its provider settings, gateway routing and context
window are the ones the backend picked it with. Here the model middleware owns the
retries (``rt.model_retries``), so a known provider's SDK retries are turned off.
"""

import json
import logging
from functools import lru_cache
from typing import Final

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_fireworks import ChatFireworks
from langchain_openai import ChatOpenAI
from pydantic import JsonValue, TypeAdapter
from runtime_api.catalog import ModelSpec
from runtime_api.models import build_chat_model

_SDK_RETRY_DISABLE: Final = {
    "openai": 0,
    "anthropic": 0,
    "fireworks": 0,
    "google_genai": 1,
}
_kwargs = TypeAdapter(dict[str, JsonValue])
logger = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def _build(model_id: str, kwargs_json: str, use_gateway: bool) -> BaseChatModel:
    kwargs = _kwargs.validate_python(json.loads(kwargs_json))
    provider = model_id.split(":", 1)[0]
    if provider in _SDK_RETRY_DISABLE:
        disabled = _SDK_RETRY_DISABLE[provider]
        if "max_retries" in kwargs and kwargs["max_retries"] != disabled:
            logger.warning(
                "Ignoring SDK retry override because model middleware owns retries",
                extra={"provider": provider},
            )
        kwargs["max_retries"] = disabled
    return build_chat_model(model_id, kwargs, use_gateway=use_gateway)


def middleware_retries(model: object) -> bool:
    """Whether this model's provider retry control is known and disabled."""
    for _ in range(10):
        bound = getattr(model, "bound", None)
        if bound is None or bound is model:
            break
        model = bound
    if isinstance(model, ChatOpenAI | ChatAnthropic | ChatFireworks):
        return model.max_retries == 0
    return (
        type(model).__module__.startswith("langchain_google_genai.")
        and getattr(model, "max_retries", None) == 1
    )


def build_model(spec: ModelSpec, *, use_gateway: bool) -> BaseChatModel:
    """The chat model for ``spec``, built once per distinct model and settings."""
    return _build(spec.model_id, json.dumps(spec.kwargs, sort_keys=True), use_gateway)
