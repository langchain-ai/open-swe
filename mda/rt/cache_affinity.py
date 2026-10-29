"""Request-local prompt-cache routing for the selected provider."""

import hashlib
import logging
from collections.abc import Awaitable, Callable, Mapping

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_fireworks.middleware import FireworksPromptCachingMiddleware
from langchain_openai import ChatOpenAI
from langgraph.config import get_config

from rt.tracing import MIDDLEWARE_TRACE_POLICY

logger = logging.getLogger(__name__)


def _thread_key(request: ModelRequest) -> str | None:
    execution = getattr(request.runtime, "execution_info", None)
    thread_id = getattr(execution, "thread_id", None)
    if not isinstance(thread_id, str) or not thread_id:
        try:
            thread_id = get_config().get("configurable", {}).get("thread_id")
        except RuntimeError:
            logger.debug("No runnable config for prompt-cache affinity", exc_info=True)
            return None
    return (
        hashlib.sha256(thread_id.encode()).hexdigest()
        if isinstance(thread_id, str) and thread_id
        else None
    )


def _has_cache_key(settings: Mapping[str, object]) -> bool:
    extra = settings.get("extra_body")
    return "prompt_cache_key" in settings or (
        isinstance(extra, Mapping) and "prompt_cache_key" in extra
    )


class OpenAICacheAffinityMiddleware(AgentMiddleware):
    """Default OpenAI cache routing to this thread without changing shared models."""

    trace_policy = MIDDLEWARE_TRACE_POLICY

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        model = request.model
        if (
            type(model) is ChatOpenAI
            and not _has_cache_key(request.model_settings)
            and not _has_cache_key(model.model_kwargs)
            and not (model.extra_body is not None and "prompt_cache_key" in model.extra_body)
            and (key := _thread_key(request)) is not None
        ):
            request = request.override(
                model_settings={**request.model_settings, "prompt_cache_key": key}
            )
        return await handler(request)


class FireworksCacheAffinityMiddleware(FireworksPromptCachingMiddleware):
    """Use the integration's context-local affinity without tracing request inputs."""

    trace_policy = MIDDLEWARE_TRACE_POLICY


def cache_affinity() -> list[AgentMiddleware]:
    """Run inside model selection so each primary or fallback gets its own defaults."""
    return [
        OpenAICacheAffinityMiddleware(),
        FireworksCacheAffinityMiddleware(unsupported_model_behavior="ignore"),
    ]
