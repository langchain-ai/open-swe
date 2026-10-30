"""The run's own system prompt, placed in each model call for the provider's prompt cache.

A provider caches a request's prefix: its tools, then its system prompt, then the
conversation. Each call's system prompt grows from the outside in: the graph's own
instructions, then what each middleware around the call adds. What more runs share
goes first:

1. the graph's instructions and what code mode and the browser add, the same for
   every run offered the same tools;
2. the run's prompt (``ModelCall.system_prompt``), the same for every thread of the
   deployment;
3. the run's skills, the same for the runs of people who see the same skills;
4. what only the thread's prompt says (``ModelCall.thread_prompt``).

For Anthropic the first two each end in a cache breakpoint, so a thread's first
call reads them from a cache another thread wrote; the prompt cache middleware
marks the end of the system prompt and of the conversation. OpenAI finds the
longest prefix it cached itself.
"""

from collections.abc import Awaitable, Callable, Mapping

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from runtime_api.loop import ModelCall

from rt.model_calls import PROMPT_CACHE_TTL
from rt.tracing import MIDDLEWARE_TRACE_POLICY

type _Block = str | dict


def _planned(request: ModelRequest) -> ModelCall | None:
    """What the run's loop planned for this call; None for a call it planned nothing for."""
    raw = request.state.get("open_swe_call")
    return ModelCall.model_validate(raw) if isinstance(raw, Mapping) else None


def _cached(block: _Block) -> dict:
    return {
        **({"type": "text", "text": block} if isinstance(block, str) else block),
        "cache_control": {"type": "ephemeral", "ttl": PROMPT_CACHE_TTL},
    }


def _appended(system: SystemMessage | None, text: str, *, cached: bool) -> SystemMessage:
    """``system`` and then ``text``; with ``cached``, each ends in a cache breakpoint."""
    blocks: list[_Block] = (
        []
        if system is None or not system.content
        else [{"type": "text", "text": system.content}]
        if isinstance(system.content, str)
        else list(system.content)
    )
    added: _Block = {"type": "text", "text": text}
    if cached:
        blocks = [*blocks[:-1], _cached(blocks[-1])] if blocks else []
        added = _cached(added)
    content = [*blocks, added]
    return (
        SystemMessage(content=content)
        if system is None
        else system.model_copy(update={"content": content})
    )


class RunPromptMiddleware(AgentMiddleware):
    """Add the run's prompt after what every run offered the same tools shares.

    Place it inside the middleware that adds to the graph's own instructions for
    every run alike, and outside the skills middleware and ``ThreadPromptMiddleware``.
    """

    trace_policy = MIDDLEWARE_TRACE_POLICY

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        planned = _planned(request)
        if planned is None or not planned.system_prompt:
            return await handler(request)
        system = _appended(
            request.system_message,
            planned.system_prompt,
            cached=isinstance(request.model, ChatAnthropic),
        )
        return await handler(request.override(system_message=system))


class ThreadPromptMiddleware(AgentMiddleware):
    """Add what only the thread's prompt says, last, after the run's skills."""

    trace_policy = MIDDLEWARE_TRACE_POLICY

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        planned = _planned(request)
        if planned is None or not planned.thread_prompt:
            return await handler(request)
        system = _appended(request.system_message, planned.thread_prompt, cached=False)
        return await handler(request.override(system_message=system))
