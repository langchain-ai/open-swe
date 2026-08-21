"""The middleware order every graph agrees on.

Lists are outermost-first: the first entry wraps every later one, and the last
entry sits closest to the provider call. That ordering used to exist only as a
comment on one of the four hand-written stacks, so it drifted; the builders here
are the ordering.
"""

from collections.abc import Sequence
from typing import Any, cast

from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.types import AgentMiddleware

from .model_call_timeout import ModelCallTimeoutMiddleware
from .repair_orphaned_tool_calls import RepairOrphanedToolCallsMiddleware
from .sanitize_fireworks_messages import SanitizeFireworksMessagesMiddleware
from .sanitize_openai_responses import SanitizeOpenAIResponsesMiddleware
from .sanitize_thinking_blocks import SanitizeThinkingBlocksMiddleware
from .sanitize_tool_inputs import SanitizeToolInputsMiddleware
from .tool_error_handler import ToolErrorMiddleware

Middleware = AgentMiddleware[Any, Any, Any]


def model_guard_middleware(*, repair_orphaned_tool_calls: bool = False) -> list[Middleware]:
    """The provider-shaped guards, innermost.

    Each one rewrites a request the provider would otherwise reject — stale
    OpenAI reasoning ids, empty Anthropic thinking blocks, Fireworks' legacy
    ``function_call`` — so they have to run after everything that can still
    change the messages. ``ModelCallTimeoutMiddleware`` is last of all, so the
    deadline covers the provider call itself and a timeout escalates outward to
    the fallback model.

    Subagents compile into their own graphs, so a parent's stack never wraps
    their model calls: every subagent spec installs this block itself.

    ``repair_orphaned_tool_calls`` is reviewer-only. ``create_deep_agent`` adds
    its own ``PatchToolCallsMiddleware``, which made the main agent's copy
    redundant (``tests/agent/test_agent_assembly_context.py`` pins that it is not
    re-added); the reviewer still installs it explicitly.
    """
    return cast(
        list[Middleware],
        [
            SanitizeFireworksMessagesMiddleware(),
            SanitizeOpenAIResponsesMiddleware(),
            SanitizeThinkingBlocksMiddleware(),
            *([RepairOrphanedToolCallsMiddleware()] if repair_orphaned_tool_calls else []),
            ModelCallTimeoutMiddleware(),
        ],
    )


def core_stack(
    *prepare: Any,
    call_limit: int,
    extras: Sequence[Any] = (),
    repair_orphaned_tool_calls: bool = False,
) -> list[Middleware]:
    """A graph's full middleware list, outermost-first.

    ``prepare`` goes first so everything after it runs against a prepared run —
    the rendered system prompt, the awaited sandbox, this run's tool set. Then
    the fixed trio: tool inputs are normalized before any tool sees them, the
    model-call budget is enforced before a tool runs, and tool exceptions become
    tool messages. ``extras`` — whatever this graph adds — therefore always runs
    inside that guarded loop, and ``model_guard_middleware`` closes the list.
    """
    return [
        *prepare,
        SanitizeToolInputsMiddleware(),
        ModelCallLimitMiddleware(run_limit=call_limit, exit_behavior="end"),
        ToolErrorMiddleware(),
        *extras,
        *model_guard_middleware(repair_orphaned_tool_calls=repair_orphaned_tool_calls),
    ]
