"""Load optional provider packages."""

from __future__ import annotations

from importlib import import_module
from typing import TypeVar

_T = TypeVar("_T")


def optional_runtime_symbols(module_name: str, *names: str) -> tuple[object | None, ...]:
    """Import symbols when the optional provider package is installed."""
    try:
        module = import_module(module_name)
    except ModuleNotFoundError as error:  # pragma: no cover - optional packages
        root_name = module_name.partition(".")[0]
        if error.name is None or not error.name.startswith(root_name):
            raise
        return (None,) * len(names)
    return tuple(getattr(module, name) for name in names)


def require_runtime_dependency(value: _T | None, package: str, purpose: str) -> _T:
    """Return a runtime dependency or raise a contextual installation error."""
    if value is None:
        raise RuntimeError(f"MDA requires `{package}` to {purpose}.")
    return value
