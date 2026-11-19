"""Chat models for the models Open SWE picked when it dispatched a run.

The backend sends model ids and provider settings, never keys. Every model calls
through the gateway as whoever its run acts for (``rt.gateway``). Both sides build
models with ``runtime_api.models``. The model middleware owns retries here.
"""

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_fireworks import ChatFireworks
from langchain_openai import ChatOpenAI
from pydantic import JsonValue, TypeAdapter
from runtime_api.catalog import ModelSpec
from runtime_api.models import build_chat_model

from rt.gateway import GATEWAY_BEARER

_SDK_RETRY_DISABLE: Final = {
    "openai": 0,
    "anthropic": 0,
    "fireworks": 0,
    "google_genai": 1,
}
# Gates ``thinking.block_binding``; the Claude API takes it, Bedrock and Vertex don't yet.
THINKING_BINDING_BETA: Final = "thinking-binding-controls-2026-08-01"
# Family-first ids (``claude-opus-5-5``) of Claude 5 and later bind preserved thinking.
_BINDS_PRESERVED_THINKING: Final = re.compile(r"claude-[a-z]+-(\d+)")
_kwargs = TypeAdapter(dict[str, JsonValue])
logger = logging.getLogger(__name__)


@dataclass
class _LoopModel:
    """A model the agent loop calls: the backend's settings, and what the loop adds to them."""

    model_id: str
    kwargs: dict[str, JsonValue]

    @property
    def provider(self) -> str:
        return self.model_id.split(":", 1)[0]

    def disable_sdk_retries(self) -> None:
        if self.provider not in _SDK_RETRY_DISABLE:
            return
        disabled = _SDK_RETRY_DISABLE[self.provider]
        if "max_retries" in self.kwargs and self.kwargs["max_retries"] != disabled:
            logger.warning(
                "Ignoring SDK retry override because model middleware owns retries",
                extra={"provider": self.provider},
            )
        self.kwargs["max_retries"] = disabled

    def drop_stale_thinking(self) -> None:
        """Have Claude drop a preserved thinking block whose prompt prefix changed.

        Claude 5 binds each thinking block to the prefix it saw. Summarizing the
        conversation rewrites that prefix, and by default the next call is then refused
        rather than the stale blocks dropped. Values the backend set are kept.
        """
        version = _BINDS_PRESERVED_THINKING.match(self.model_id.partition(":")[2])
        thinking = self.kwargs.get("thinking")
        betas = self.kwargs.get("betas", [])
        if (
            self.provider != "anthropic"
            or version is None
            or int(version.group(1)) < 5
            or not isinstance(thinking, dict)
            or thinking.get("type") not in {"adaptive", "enabled"}
            or not isinstance(betas, list)
        ):
            return
        binding = thinking.get("block_binding", {})
        if not isinstance(binding, dict):
            return
        self.kwargs["thinking"] = {
            **thinking,
            "block_binding": {"prefix_mismatch_behavior": "drop_block", **binding},
        }
        if THINKING_BINDING_BETA not in betas:
            self.kwargs["betas"] = [*betas, THINKING_BINDING_BETA]

    def build(self) -> BaseChatModel:
        return build_chat_model(self.model_id, self.kwargs, gateway=GATEWAY_BEARER)


@lru_cache(maxsize=32)
def _build(model_id: str, kwargs_json: str) -> BaseChatModel:
    model = _LoopModel(model_id, _kwargs.validate_python(json.loads(kwargs_json)))
    model.disable_sdk_retries()
    model.drop_stale_thinking()
    return model.build()


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


def build_model(spec: ModelSpec) -> BaseChatModel:
    """The chat model for ``spec``, built once per distinct model and settings."""
    return _build(spec.model_id, json.dumps(spec.kwargs, sort_keys=True))
