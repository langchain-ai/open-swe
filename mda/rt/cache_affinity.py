"""Request-local prompt-cache routing shared by graphs using the same model."""

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_fireworks import ChatFireworks
from langchain_openai import ChatOpenAI

from rt.tracing import MIDDLEWARE_TRACE_POLICY


def _has_override(settings: Mapping[str, object], *, fireworks: bool) -> bool:
    extra = settings.get("extra_body")
    body = {**settings, **extra} if isinstance(extra, Mapping) else settings
    if "prompt_cache_key" in body or (fireworks and "user" in body):
        return True
    headers = settings.get("extra_headers")
    return (
        fireworks
        and headers is not None
        and (
            not isinstance(headers, Mapping)
            or any(isinstance(key, str) and key.lower() == "x-session-affinity" for key in headers)
        )
    )


class CacheAffinityMiddleware(AgentMiddleware):
    """Group common prefixes without storing generated settings on shared models."""

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
        if type(model) is not ChatOpenAI and type(model) is not ChatFireworks:
            return await handler(request)
        fireworks = isinstance(model, ChatFireworks)
        settings = dict(model.model_kwargs)
        if isinstance(model, ChatOpenAI) and model.extra_body is not None:
            settings["extra_body"] = model.extra_body
        if not any(
            _has_override(s, fireworks=fireworks) for s in (settings, request.model_settings)
        ):
            group = json.dumps(
                ["open-swe-prefix-v1", self._graph, type(model).__name__, model.model_name]
            )
            key = hashlib.sha256(group.encode()).hexdigest()
            request = request.override(
                model_settings={**request.model_settings, "prompt_cache_key": key}
            )
        return await handler(request)
