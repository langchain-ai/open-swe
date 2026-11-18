"""The run's own system prompt, placed in each model call for the provider's prompt cache.

A provider caches a request's prefix: its tools, then its system prompt, then the
conversation. Each call's system prompt grows from the outside in: the graph's own
instructions, then what each middleware around the call adds. What more runs share
goes first:

1. the graph's instructions and what code mode and the browser add, the same for
   every run offered the same tools;
2. the run's prompt (``ModelCall.system_prompt``), the same for every thread of the
   deployment;
3. the workspace's prompt (``ModelCall.workspace_prompt``), the same for every thread
   of the workspace;
4. the run's skills, the same for the runs of people who see the same skills;
5. what only the thread's prompt says (``ModelCall.thread_prompt``).

The last two of the first three each end in a cache breakpoint, so a thread's first
call reads them from a cache another thread wrote. For Anthropic the prompt cache
middleware marks the end of the system prompt and of the conversation, and a request
may have no more than those four. OpenAI's GPT-5.6 and later cache only up to a
breakpoint too, and the one it places itself ends the request, which no other thread's
request starts with; it writes that one and up to three explicit ones per request.
"""

import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Final

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from runtime_api.loop import ModelCall

from rt.model_calls import PROMPT_CACHE_TTL
from rt.tracing import MIDDLEWARE_TRACE_POLICY

# The first OpenAI models that take ``prompt_cache_breakpoint``; earlier ones reject it.
_OPENAI_BREAKPOINTS_SINCE: Final = (5, 6)
_GPT_VERSION: Final = re.compile(r"gpt-(\d+)(?:\.(\d+))?(?:-|$)")

type _Block = str | dict


def _planned(request: ModelRequest) -> ModelCall | None:
    """What the run's loop planned for this call; None for a call it planned nothing for."""
    raw = request.state.get("open_swe_call")
    return ModelCall.model_validate(raw) if isinstance(raw, Mapping) else None


def _appended(
    system: SystemMessage | None, texts: list[str], *, cached: int, marker: dict | None = None
) -> SystemMessage:
    """``system`` and then each of ``texts``; the last ``cached`` blocks end in ``marker``."""
    blocks: list[_Block] = [
        *(
            []
            if system is None or not system.content
            else [{"type": "text", "text": system.content}]
            if isinstance(system.content, str)
            else list(system.content)
        ),
        *({"type": "text", "text": text} for text in texts),
    ]
    split = max(len(blocks) - cached, 0)
    content = (
        blocks
        if marker is None
        else [
            *blocks[:split],
            *(
                {**({"type": "text", "text": block} if isinstance(block, str) else block), **marker}
                for block in blocks[split:]
            ),
        ]
    )
    return (
        SystemMessage(content=content)
        if system is None
        else system.model_copy(update={"content": content})
    )


class RunPromptMiddleware(AgentMiddleware):
    """Add the run's prompt, then the workspace's, after what every run offered the same
    tools shares.

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
        texts = [text for text in (planned.system_prompt, planned.workspace_prompt) if text]
        system = _appended(
            request.system_message, texts, cached=2, marker=self._breakpoint(request.model)
        )
        return await handler(request.override(system_message=system))

    @staticmethod
    def _breakpoint(model: object) -> dict | None:
        """What ends a block in a cache breakpoint for ``model``; None for a model without them."""
        if isinstance(model, ChatAnthropic):
            return {"cache_control": {"type": "ephemeral", "ttl": PROMPT_CACHE_TTL}}
        version = _GPT_VERSION.match(model.model_name) if isinstance(model, ChatOpenAI) else None
        if version is not None and (
            (int(version[1]), int(version[2] or 0)) >= _OPENAI_BREAKPOINTS_SINCE
        ):
            # Without a ttl it lives as long as the request's, OpenAI's 30m default.
            return {"prompt_cache_breakpoint": {"mode": "explicit"}}
        return None


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
        system = _appended(request.system_message, [planned.thread_prompt], cached=0)
        return await handler(request.override(system_message=system))
