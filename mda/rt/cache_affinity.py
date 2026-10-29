"""Request-local cache routing with provider-specific affinity scopes."""

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_fireworks.middleware import FireworksPromptCachingMiddleware
from langchain_openai import ChatOpenAI

from rt.tracing import MIDDLEWARE_TRACE_POLICY


def _has_override(settings: Mapping[str, object]) -> bool:
    extra = settings.get("extra_body")
    return "prompt_cache_key" in settings or (
        isinstance(extra, Mapping) and "prompt_cache_key" in extra
    )


class CacheAffinityMiddleware(AgentMiddleware):
    """Group OpenAI prefixes without storing generated settings on shared models."""

    trace_policy = MIDDLEWARE_TRACE_POLICY

    def __init__(self, graph: str) -> None:
        super().__init__()
        self._graph = graph

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        model = request.model
        if type(model) is not ChatOpenAI:
            return await handler(request)
        if (
            not _has_override(model.model_kwargs)
            and not _has_override(request.model_settings)
            and not (model.extra_body is not None and "prompt_cache_key" in model.extra_body)
        ):
            group = json.dumps(
                ["open-swe-prefix-v1", self._graph, type(model).__name__, model.model_name]
            )
            key = hashlib.sha256(group.encode()).hexdigest()
            request = request.override(
                model_settings={**request.model_settings, "prompt_cache_key": key}
            )
        return await handler(request)


class FireworksCacheAffinityMiddleware(FireworksPromptCachingMiddleware):
    """Use the integration's thread affinity without tracing request inputs."""

    trace_policy = MIDDLEWARE_TRACE_POLICY
