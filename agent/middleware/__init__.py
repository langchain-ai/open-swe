import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

_MIDDLEWARE_MODULES = {
    "check_message_queue_before_model": ".check_message_queue",
    "DynamicToolMiddleware": ".dynamic_tools",
    "IntegrationGroup": ".dynamic_tools",
    "ExcludeToolsMiddleware": ".exclude_tools",
    "ModelCallTimeoutMiddleware": ".model_call_timeout",
    "ModelFallbackMiddleware": ".model_fallback",
    "notify_step_limit_reached": ".notify_step_limit",
    "PlanModeMiddleware": ".plan_mode",
    "PrepareRunState": ".prepare_run",
    "BasePrepareRunMiddleware": ".prepare_run",
    "PullRequestCreationGuardMiddleware": ".pr_creation_guard",
    "refresh_github_proxy_before_model": ".refresh_github_proxy",
    "RepairOrphanedToolCallsMiddleware": ".repair_orphaned_tool_calls",
    "SanitizeFireworksMessagesMiddleware": ".sanitize_fireworks_messages",
    "SanitizeOpenAIResponsesMiddleware": ".sanitize_openai_responses",
    "SanitizeThinkingBlocksMiddleware": ".sanitize_thinking_blocks",
    "SanitizeToolInputsMiddleware": ".sanitize_tool_inputs",
    "SlackTranscriptMiddleware": ".slack_transcript",
    "StableToolResultOrderMiddleware": ".stable_tool_order",
    "settle_review_check_on_exit": ".settle_review_check",
    "SubdirAgentsReadMiddleware": ".subdir_agents",
    "task_on_failure": ".task_retry",
    "task_retry_on": ".task_retry",
    "TimeoutWrapupMiddleware": ".timeout_wrapup",
    "ToolErrorMiddleware": ".tool_error_handler",
    "WorkflowPushGuardMiddleware": ".workflow_push_guard",
}

__all__ = [
    "DynamicToolMiddleware",
    "ExcludeToolsMiddleware",
    "IntegrationGroup",
    "ModelCallTimeoutMiddleware",
    "ModelFallbackMiddleware",
    "BasePrepareRunMiddleware",
    "PlanModeMiddleware",
    "PrepareRunState",
    "PullRequestCreationGuardMiddleware",
    "RepairOrphanedToolCallsMiddleware",
    "SanitizeFireworksMessagesMiddleware",
    "SanitizeOpenAIResponsesMiddleware",
    "SanitizeThinkingBlocksMiddleware",
    "SanitizeToolInputsMiddleware",
    "SlackTranscriptMiddleware",
    "StableToolResultOrderMiddleware",
    "SubdirAgentsReadMiddleware",
    "ToolErrorMiddleware",
    "TimeoutWrapupMiddleware",
    "WorkflowPushGuardMiddleware",
    "check_message_queue_before_model",
    "notify_step_limit_reached",
    "refresh_github_proxy_before_model",
    "settle_review_check_on_exit",
    "task_on_failure",
    "task_retry_on",
]

if TYPE_CHECKING:
    from .check_message_queue import check_message_queue_before_model
    from .dynamic_tools import DynamicToolMiddleware, IntegrationGroup
    from .exclude_tools import ExcludeToolsMiddleware
    from .model_call_timeout import ModelCallTimeoutMiddleware
    from .model_fallback import ModelFallbackMiddleware
    from .notify_step_limit import notify_step_limit_reached
    from .plan_mode import PlanModeMiddleware
    from .pr_creation_guard import PullRequestCreationGuardMiddleware
    from .prepare_run import BasePrepareRunMiddleware, PrepareRunState
    from .refresh_github_proxy import refresh_github_proxy_before_model
    from .repair_orphaned_tool_calls import RepairOrphanedToolCallsMiddleware
    from .sanitize_fireworks_messages import SanitizeFireworksMessagesMiddleware
    from .sanitize_openai_responses import SanitizeOpenAIResponsesMiddleware
    from .sanitize_thinking_blocks import SanitizeThinkingBlocksMiddleware
    from .sanitize_tool_inputs import SanitizeToolInputsMiddleware
    from .settle_review_check import settle_review_check_on_exit
    from .slack_transcript import SlackTranscriptMiddleware
    from .stable_tool_order import StableToolResultOrderMiddleware
    from .subdir_agents import SubdirAgentsReadMiddleware
    from .task_retry import task_on_failure, task_retry_on
    from .timeout_wrapup import TimeoutWrapupMiddleware
    from .tool_error_handler import ToolErrorMiddleware
    from .workflow_push_guard import WorkflowPushGuardMiddleware


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
