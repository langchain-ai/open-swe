import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

_MIDDLEWARE_MODULES = {
    "check_message_queue_before_model": ".check_message_queue",
    "deliver_event_matches_before_model": ".deliver_event_matches",
    "DescribeCommandsMiddleware": ".describe_commands",
    "DynamicToolMiddleware": ".dynamic_tools",
    "IntegrationGroup": ".dynamic_tools",
    "ExcludeToolsMiddleware": ".exclude_tools",
    "ModelCallTimeoutMiddleware": ".model_call_timeout",
    "ModelErrorMiddleware": ".model_errors",
    "ModelFallbackMiddleware": ".model_fallback",
    "ModelSelectionMiddleware": ".model_selection",
    "notify_step_limit_reached": ".notify_step_limit",
    "PrepareRunState": ".prepare_run",
    "BasePrepareRunMiddleware": ".prepare_run",
    "PullRequestCreationGuardMiddleware": ".pr_creation_guard",
    "record_run_usage": ".record_run_usage",
    "refresh_github_proxy_before_model": ".refresh_github_proxy",
    "RepairOrphanedToolCallsMiddleware": ".repair_orphaned_tool_calls",
    "RequireUserReplyMiddleware": ".require_user_reply",
    "SanitizeFireworksMessagesMiddleware": ".sanitize_fireworks_messages",
    "SanitizeOpenAIResponsesMiddleware": ".sanitize_openai_responses",
    "SanitizeThinkingBlocksMiddleware": ".sanitize_thinking_blocks",
    "StableToolResultOrderMiddleware": ".stable_tool_order",
    "settle_review_check_on_exit": ".settle_review_check",
    "SubdirAgentsReadMiddleware": ".subdir_agents",
    "task_on_failure": ".task_retry",
    "task_retry_on": ".task_retry",
    "ToolErrorMiddleware": ".tool_error_handler",
    "ValidateImageReadsMiddleware": ".validate_image_reads",
    "WorkflowPushGuardMiddleware": ".workflow_push_guard",
    "WorkspaceSkillsMiddleware": ".workspace_skills",
}

__all__ = [
    "DescribeCommandsMiddleware",
    "DynamicToolMiddleware",
    "ExcludeToolsMiddleware",
    "IntegrationGroup",
    "ModelCallTimeoutMiddleware",
    "ModelErrorMiddleware",
    "ModelFallbackMiddleware",
    "ModelSelectionMiddleware",
    "BasePrepareRunMiddleware",
    "PrepareRunState",
    "PullRequestCreationGuardMiddleware",
    "RepairOrphanedToolCallsMiddleware",
    "RequireUserReplyMiddleware",
    "SanitizeFireworksMessagesMiddleware",
    "SanitizeOpenAIResponsesMiddleware",
    "SanitizeThinkingBlocksMiddleware",
    "StableToolResultOrderMiddleware",
    "SubdirAgentsReadMiddleware",
    "ToolErrorMiddleware",
    "ValidateImageReadsMiddleware",
    "WorkflowPushGuardMiddleware",
    "WorkspaceSkillsMiddleware",
    "check_message_queue_before_model",
    "deliver_event_matches_before_model",
    "notify_step_limit_reached",
    "record_run_usage",
    "refresh_github_proxy_before_model",
    "settle_review_check_on_exit",
    "task_on_failure",
    "task_retry_on",
]

if TYPE_CHECKING:
    from openswe.middleware.check_message_queue import check_message_queue_before_model
    from openswe.middleware.deliver_event_matches import deliver_event_matches_before_model
    from openswe.middleware.describe_commands import DescribeCommandsMiddleware
    from openswe.middleware.dynamic_tools import DynamicToolMiddleware, IntegrationGroup
    from openswe.middleware.exclude_tools import ExcludeToolsMiddleware
    from openswe.middleware.model_call_timeout import ModelCallTimeoutMiddleware
    from openswe.middleware.model_errors import ModelErrorMiddleware
    from openswe.middleware.model_fallback import ModelFallbackMiddleware
    from openswe.middleware.model_selection import ModelSelectionMiddleware
    from openswe.middleware.notify_step_limit import notify_step_limit_reached
    from openswe.middleware.pr_creation_guard import PullRequestCreationGuardMiddleware
    from openswe.middleware.prepare_run import BasePrepareRunMiddleware, PrepareRunState
    from openswe.middleware.record_run_usage import record_run_usage
    from openswe.middleware.refresh_github_proxy import refresh_github_proxy_before_model
    from openswe.middleware.repair_orphaned_tool_calls import RepairOrphanedToolCallsMiddleware
    from openswe.middleware.require_user_reply import RequireUserReplyMiddleware
    from openswe.middleware.sanitize_fireworks_messages import SanitizeFireworksMessagesMiddleware
    from openswe.middleware.sanitize_openai_responses import SanitizeOpenAIResponsesMiddleware
    from openswe.middleware.sanitize_thinking_blocks import SanitizeThinkingBlocksMiddleware
    from openswe.middleware.settle_review_check import settle_review_check_on_exit
    from openswe.middleware.stable_tool_order import StableToolResultOrderMiddleware
    from openswe.middleware.subdir_agents import SubdirAgentsReadMiddleware
    from openswe.middleware.task_retry import task_on_failure, task_retry_on
    from openswe.middleware.tool_error_handler import ToolErrorMiddleware
    from openswe.middleware.validate_image_reads import ValidateImageReadsMiddleware
    from openswe.middleware.workflow_push_guard import WorkflowPushGuardMiddleware
    from openswe.middleware.workspace_skills import WorkspaceSkillsMiddleware


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
