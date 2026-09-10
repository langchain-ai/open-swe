import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

_MIDDLEWARE_MODULES = {
    "DynamicToolMiddleware": ".dynamic_tools",
    "IntegrationGroup": ".dynamic_tools",
    "ExcludeToolsMiddleware": ".exclude_tools",
    "ModelCallTimeoutMiddleware": ".model_call_timeout",
    "ModelErrorMiddleware": ".model_errors",
    "ModelFallbackMiddleware": ".model_fallback",
    "ModelSelectionMiddleware": ".model_selection",
    "PlanModeMiddleware": ".plan_mode",
    "PrepareRunState": ".prepare_run",
    "BasePrepareRunMiddleware": ".prepare_run",
    "RepairOrphanedToolCallsMiddleware": ".repair_orphaned_tool_calls",
    "SanitizeFireworksMessagesMiddleware": ".sanitize_fireworks_messages",
    "SanitizeOpenAIResponsesMiddleware": ".sanitize_openai_responses",
    "SanitizeThinkingBlocksMiddleware": ".sanitize_thinking_blocks",
    "SanitizeToolInputsMiddleware": ".sanitize_tool_inputs",
    "StableToolResultOrderMiddleware": ".stable_tool_order",
    "SubdirAgentsReadMiddleware": ".subdir_agents",
    "SandboxFailureNotifier": ".tool_error_handler",
    "ToolErrorMiddleware": ".tool_error_handler",
    "CodingAgentMiddleware": ".trace",
    "task_on_failure": ".task_retry",
    "task_retry_on": ".task_retry",
    "TimeoutWrapupMiddleware": ".timeout_wrapup",
}

__all__ = [
    "BasePrepareRunMiddleware",
    "CodingAgentMiddleware",
    "DynamicToolMiddleware",
    "ExcludeToolsMiddleware",
    "IntegrationGroup",
    "ModelCallTimeoutMiddleware",
    "ModelErrorMiddleware",
    "ModelFallbackMiddleware",
    "ModelSelectionMiddleware",
    "PlanModeMiddleware",
    "PrepareRunState",
    "RepairOrphanedToolCallsMiddleware",
    "SandboxFailureNotifier",
    "SanitizeFireworksMessagesMiddleware",
    "SanitizeOpenAIResponsesMiddleware",
    "SanitizeThinkingBlocksMiddleware",
    "SanitizeToolInputsMiddleware",
    "StableToolResultOrderMiddleware",
    "SubdirAgentsReadMiddleware",
    "TimeoutWrapupMiddleware",
    "ToolErrorMiddleware",
    "task_on_failure",
    "task_retry_on",
]

if TYPE_CHECKING:
    from coding_agent.middleware.dynamic_tools import DynamicToolMiddleware, IntegrationGroup
    from coding_agent.middleware.exclude_tools import ExcludeToolsMiddleware
    from coding_agent.middleware.model_call_timeout import ModelCallTimeoutMiddleware
    from coding_agent.middleware.model_errors import ModelErrorMiddleware
    from coding_agent.middleware.model_fallback import ModelFallbackMiddleware
    from coding_agent.middleware.model_selection import ModelSelectionMiddleware
    from coding_agent.middleware.plan_mode import PlanModeMiddleware
    from coding_agent.middleware.prepare_run import BasePrepareRunMiddleware, PrepareRunState
    from coding_agent.middleware.repair_orphaned_tool_calls import (
        RepairOrphanedToolCallsMiddleware,
    )
    from coding_agent.middleware.sanitize_fireworks_messages import (
        SanitizeFireworksMessagesMiddleware,
    )
    from coding_agent.middleware.sanitize_openai_responses import (
        SanitizeOpenAIResponsesMiddleware,
    )
    from coding_agent.middleware.sanitize_thinking_blocks import SanitizeThinkingBlocksMiddleware
    from coding_agent.middleware.sanitize_tool_inputs import SanitizeToolInputsMiddleware
    from coding_agent.middleware.stable_tool_order import StableToolResultOrderMiddleware
    from coding_agent.middleware.subdir_agents import SubdirAgentsReadMiddleware
    from coding_agent.middleware.task_retry import task_on_failure, task_retry_on
    from coding_agent.middleware.timeout_wrapup import TimeoutWrapupMiddleware
    from coding_agent.middleware.tool_error_handler import (
        SandboxFailureNotifier,
        ToolErrorMiddleware,
    )
    from coding_agent.middleware.trace import CodingAgentMiddleware


def _load_export(name: str) -> Any:
    module_name = _MIDDLEWARE_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


class _LazyMiddlewareModule(ModuleType):
    def __getattribute__(self, name: str) -> Any:
        module_map = ModuleType.__getattribute__(self, "__dict__").get("_MIDDLEWARE_MODULES", {})
        if name not in module_map:
            return ModuleType.__getattribute__(self, name)
        existing = ModuleType.__getattribute__(self, "__dict__").get(name)
        if existing is not None and not isinstance(existing, ModuleType):
            return existing
        return _load_export(name)


def __getattr__(name: str) -> Any:
    return _load_export(name)


sys.modules[__name__].__class__ = _LazyMiddlewareModule
