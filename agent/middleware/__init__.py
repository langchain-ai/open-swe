import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

_MIDDLEWARE_MODULES = {
    "check_message_queue_before_model": ".check_message_queue",
    "notify_step_limit_reached": ".notify_step_limit",
    "PullRequestCreationGuardMiddleware": ".pr_creation_guard",
    "record_run_usage": ".record_run_usage",
    "refresh_github_proxy_before_model": ".refresh_github_proxy",
    "settle_review_check_on_exit": ".settle_review_check",
    "WorkflowPushGuardMiddleware": ".workflow_push_guard",
}

__all__ = [
    "PullRequestCreationGuardMiddleware",
    "WorkflowPushGuardMiddleware",
    "check_message_queue_before_model",
    "notify_step_limit_reached",
    "record_run_usage",
    "refresh_github_proxy_before_model",
    "settle_review_check_on_exit",
]

if TYPE_CHECKING:
    from agent.middleware.check_message_queue import check_message_queue_before_model
    from agent.middleware.notify_step_limit import notify_step_limit_reached
    from agent.middleware.pr_creation_guard import PullRequestCreationGuardMiddleware
    from agent.middleware.record_run_usage import record_run_usage
    from agent.middleware.refresh_github_proxy import refresh_github_proxy_before_model
    from agent.middleware.settle_review_check import settle_review_check_on_exit
    from agent.middleware.workflow_push_guard import WorkflowPushGuardMiddleware


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
