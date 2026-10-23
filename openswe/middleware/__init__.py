import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

_MIDDLEWARE_MODULES = {
    "queued_follow_ups": ".check_message_queue",
    "owed_event_matches": ".deliver_event_matches",
    "DynamicToolMiddleware": ".dynamic_tools",
    "IntegrationGroup": ".dynamic_tools",
    "ExcludeToolsMiddleware": ".exclude_tools",
    "ModelErrorMiddleware": ".model_errors",
    "ModelFallbackMiddleware": ".model_fallback",
    "ModelSelectionMiddleware": ".model_selection",
    "notify_step_limit_reached": ".notify_step_limit",
    "PrepareRunState": ".prepare_run",
    "BasePrepareRunMiddleware": ".prepare_run",
    "PullRequestCreationGuardMiddleware": ".pr_creation_guard",
    "record_run_usage": ".record_run_usage",
    "github_access_refresh": ".refresh_github_proxy",
    "RepairOrphanedToolCallsMiddleware": ".repair_orphaned_tool_calls",
    "RequireUserReplyMiddleware": ".require_user_reply",
    "settle_review_check_on_exit": ".settle_review_check",
    "WorkflowPushGuardMiddleware": ".workflow_push_guard",
}

__all__ = [
    "DynamicToolMiddleware",
    "ExcludeToolsMiddleware",
    "IntegrationGroup",
    "ModelErrorMiddleware",
    "ModelFallbackMiddleware",
    "ModelSelectionMiddleware",
    "BasePrepareRunMiddleware",
    "PrepareRunState",
    "PullRequestCreationGuardMiddleware",
    "RepairOrphanedToolCallsMiddleware",
    "RequireUserReplyMiddleware",
    "WorkflowPushGuardMiddleware",
    "queued_follow_ups",
    "owed_event_matches",
    "notify_step_limit_reached",
    "record_run_usage",
    "github_access_refresh",
    "settle_review_check_on_exit",
]

if TYPE_CHECKING:
    from openswe.middleware.check_message_queue import queued_follow_ups
    from openswe.middleware.deliver_event_matches import owed_event_matches
    from openswe.middleware.dynamic_tools import DynamicToolMiddleware, IntegrationGroup
    from openswe.middleware.exclude_tools import ExcludeToolsMiddleware
    from openswe.middleware.model_errors import ModelErrorMiddleware
    from openswe.middleware.model_fallback import ModelFallbackMiddleware
    from openswe.middleware.model_selection import ModelSelectionMiddleware
    from openswe.middleware.notify_step_limit import notify_step_limit_reached
    from openswe.middleware.pr_creation_guard import PullRequestCreationGuardMiddleware
    from openswe.middleware.prepare_run import BasePrepareRunMiddleware, PrepareRunState
    from openswe.middleware.record_run_usage import record_run_usage
    from openswe.middleware.refresh_github_proxy import github_access_refresh
    from openswe.middleware.repair_orphaned_tool_calls import RepairOrphanedToolCallsMiddleware
    from openswe.middleware.require_user_reply import RequireUserReplyMiddleware
    from openswe.middleware.settle_review_check import settle_review_check_on_exit
    from openswe.middleware.workflow_push_guard import WorkflowPushGuardMiddleware


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
