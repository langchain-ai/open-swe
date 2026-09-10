import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

_TOOL_MODULES = {
    "background_tools": ".background_execute",
    "create_sandbox_file_download_url": ".create_sandbox_file_download_url",
    "create_sandbox_service_url": ".create_sandbox_service_url",
    "fetch_url": ".fetch_url",
    "http_request": ".http_request",
    "output_iframe": ".output_iframe",
    "web_search": ".web_search",
}

__all__ = [
    "background_tools",
    "create_sandbox_file_download_url",
    "create_sandbox_service_url",
    "fetch_url",
    "http_request",
    "output_iframe",
    "web_search",
]

if TYPE_CHECKING:
    from coding_agent.tools.background_execute import background_tools
    from coding_agent.tools.create_sandbox_file_download_url import (
        create_sandbox_file_download_url,
    )
    from coding_agent.tools.create_sandbox_service_url import create_sandbox_service_url
    from coding_agent.tools.fetch_url import fetch_url
    from coding_agent.tools.http_request import http_request
    from coding_agent.tools.output_iframe import output_iframe
    from coding_agent.tools.web_search import web_search


def _load_export(name: str) -> Any:
    module_name = _TOOL_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


class _LazyToolsModule(ModuleType):
    def __getattribute__(self, name: str) -> Any:
        module_map = ModuleType.__getattribute__(self, "__dict__").get("_TOOL_MODULES", {})
        if name not in module_map:
            return ModuleType.__getattribute__(self, name)
        # Prefer public exports over same-named submodule attributes set by importlib.
        existing = ModuleType.__getattribute__(self, "__dict__").get(name)
        if existing is not None and not isinstance(existing, ModuleType):
            return existing
        return _load_export(name)


def __getattr__(name: str) -> Any:
    return _load_export(name)


sys.modules[__name__].__class__ = _LazyToolsModule
