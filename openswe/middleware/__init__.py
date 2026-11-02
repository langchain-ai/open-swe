import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

_MIDDLEWARE_MODULES = {
    "DynamicToolMiddleware": ".dynamic_tools",
    "IntegrationGroup": ".dynamic_tools",
    "ModelSelectionMiddleware": ".model_selection",
    "PrepareRunState": ".prepare_run",
    "BasePrepareRunMiddleware": ".prepare_run",
    "PullRequestCreationGuardMiddleware": ".pr_creation_guard",
    "record_run_usage": ".record_run_usage",
    "WorkflowPushGuardMiddleware": ".workflow_push_guard",
}

__all__ = [
    "DynamicToolMiddleware",
    "IntegrationGroup",
    "ModelSelectionMiddleware",
    "BasePrepareRunMiddleware",
    "PrepareRunState",
    "PullRequestCreationGuardMiddleware",
    "WorkflowPushGuardMiddleware",
    "record_run_usage",
]

if TYPE_CHECKING:
    from openswe.middleware.dynamic_tools import DynamicToolMiddleware, IntegrationGroup
    from openswe.middleware.model_selection import ModelSelectionMiddleware
    from openswe.middleware.pr_creation_guard import PullRequestCreationGuardMiddleware
    from openswe.middleware.prepare_run import BasePrepareRunMiddleware, PrepareRunState
    from openswe.middleware.record_run_usage import record_run_usage
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
